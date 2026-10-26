"""The daemon of a local web UI: an HTTP server on 127.0.0.1, guarded.

A UI builds an `App`, declares its routes, and calls `serve(app)`:

    app = App("hello", header="X-Hello", port=8770, code_dirs=[HERE])

    @app.route("GET", "/api/items/{item}")
    def item(req: Request) -> object:
        return {"item": req.params["item"]}          # a dict or a list: JSON

    serve(app)

**The guards are not optional, and `App` applies them before any route**, in
this order, so that no UI can forget one or put them in the wrong order:

| Guard                     | Refuses                                          | Closes |
|---------------------------|--------------------------------------------------|--------|
| `Host` names the loopback | DNS rebinding: a foreign name resolving to us    | 403    |
| the UI's own header       | any write (not GET/HEAD) without it: a web page  | 403    |
|                           | elsewhere cannot add it without a CORS preflight,|        |
|                           | which this server never grants                   |        |
| `Origin`, when present    | a write from a page of another origin            | 403    |
| bounded body              | more than `max_body` bytes, or a chunked body    | 413    |

A refusal closes the connection: the body it left unread would otherwise be
parsed as the next request of a kept-alive connection. Every answer carries
`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and refuses to be
framed (`X-Frame-Options: DENY`, `frame-ancestors 'none'`): a foreign page
cannot put the UI in an invisible frame and borrow the user's clicks.

The header name is a required argument with no default: a UI that forgets it
does not start. It must start with `X-`: a CORS-safelisted name such as
`Content-Type` would let a foreign page send it without a preflight.

**The socket is bound to 127.0.0.1, and nothing can change that**: there is no
host parameter. The URL handed out is `http://127.0.0.1:<port>`, never
`localhost`: under WSL with mirrored networking, Windows resolves `localhost`
to ::1 first, which a socket bound to 127.0.0.1 in WSL never receives
(measured in docpin: 127.0.0.1 answers in 22 ms, localhost times out).

Reserved paths, under `/plinth/`:

    GET /plinth/status          {name, stale, plinth, port}: what the page polls
    GET /plinth/static/<name>   ihm.css, ihm.js: only a name listed in static/

A UI serves its own files with `app.add_static(prefix, folder)`: same rule,
only a file listed under that folder, so no request can leave it.

**`stale`**: the fingerprint (name, size, date) of the modules of plinth and
of the UI's `code_dirs`, taken at start, compared with the disk. The page is
read from disk at every request, the Python once: after an update, a new page
meets an old daemon, and the page says so instead of drawing wrong things.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import signal
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__

log = logging.getLogger("plinth")

LOOPBACK = "127.0.0.1"
MAX_BODY = 2 * 1024 * 1024
RESERVED = "/plinth"
SAFE_METHODS = frozenset({"GET", "HEAD"})
ROUTE_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
# The folder of plinth's own modules: part of the fingerprint behind `stale`.
CODE_DIR = Path(__file__).resolve().parent
HEADER_RE = re.compile(r"X-[A-Za-z0-9][A-Za-z0-9-]*")
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,40}")
SEGMENT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(:path)?\}")
# Headers App always sets; a route cannot replace them.
GUARDED_HEADERS = frozenset({
    "cache-control", "x-content-type-options", "x-frame-options", "content-security-policy",
    "referrer-policy", "content-length", "content-type", "connection",
})


class ServerError(Exception):
    """The App is misconfigured, or the daemon could not start."""


class Refused(Exception):
    """Raised by a route (or a guard) to answer `status` with `message`.

    The connection is closed after the answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = HTTPStatus(status)


# -- answers ----------------------------------------------------------------

@dataclass(frozen=True)
class Response:
    """What a route returns when a JSON body is not what it wants."""

    status: int = HTTPStatus.OK
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, _ in self.headers:
            if name.lower() in GUARDED_HEADERS:
                raise ServerError(f"a route cannot set the {name} header: App sets it")


def json_response(data: object, status: int = HTTPStatus.OK) -> Response:
    return Response(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")


def html_response(text: str, status: int = HTTPStatus.OK) -> Response:
    return Response(status, text.encode("utf-8"), "text/html; charset=utf-8")


def content_type(name: str) -> str:
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith(".mjs"):
        kind = "text/javascript"
    textual = kind.startswith("text/") or kind in ("application/javascript", "application/json",
                                                    "image/svg+xml")
    return f"{kind}; charset=utf-8" if textual else kind


def file_response(resource: Traversable | Path) -> Response:
    """A file read now (never cached by the daemon), typed by its extension."""
    return Response(HTTPStatus.OK, resource.read_bytes(), content_type(resource.name))


def redirect(location: str) -> Response:
    return Response(HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
                    (("Location", location),))


# -- requests ---------------------------------------------------------------

@dataclass
class Request:
    """One request, as a route sees it. The guards have already passed."""

    method: str
    path: str
    segments: tuple[str, ...]
    params: dict[str, str]
    query: dict[str, list[str]]
    headers: Message
    body: bytes
    app: App

    def json(self) -> Any:
        """The body as JSON (None when empty); a 400 if it is not JSON."""
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Refused(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}") from exc

    def json_object(self) -> dict[str, Any]:
        """The body as a JSON object ({} when empty); a 400 otherwise."""
        data = self.json()
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, "a JSON object is expected")
        return data

    def arg(self, name: str, default: str = "") -> str:
        """The first value of a query parameter."""
        return self.query.get(name, [default])[0]


RouteHandler = Callable[[Request], object]


@dataclass(frozen=True)
class _Route:
    method: str
    pattern: str
    parts: tuple[str, ...]
    handler: RouteHandler

    def match(self, segments: Sequence[str]) -> dict[str, str] | None:
        params: dict[str, str] = {}
        for index, part in enumerate(self.parts):
            capture = SEGMENT_RE.fullmatch(part)
            if capture and capture.group(2):          # {name:path}: the rest, joined
                rest = segments[index:]
                if not rest:
                    return None
                params[capture.group(1)] = "/".join(rest)
                return params
            if index >= len(segments):
                return None
            if capture:
                params[capture.group(1)] = segments[index]
            elif part != segments[index]:
                return None
        return params if len(segments) == len(self.parts) else None


def _parse_pattern(pattern: str) -> tuple[str, ...]:
    if not pattern.startswith("/"):
        raise ServerError(f"route {pattern!r}: a pattern starts with '/'")
    parts = tuple(p for p in pattern.split("/") if p)
    names = set()
    for index, part in enumerate(parts):
        if "{" in part or "}" in part:
            capture = SEGMENT_RE.fullmatch(part)
            if not capture:
                raise ServerError(f"route {pattern!r}: {part!r} is not {{name}} or {{name:path}}")
            if capture.group(2) and index != len(parts) - 1:
                raise ServerError(f"route {pattern!r}: {{name:path}} must come last")
            if capture.group(1) in names:
                raise ServerError(f"route {pattern!r}: {capture.group(1)!r} twice")
            names.add(capture.group(1))
    return parts


def _shape(parts: tuple[str, ...]) -> tuple[str, ...]:
    """The pattern without its capture names: /x/{a} and /x/{b} are one route."""
    shaped = []
    for part in parts:
        capture = SEGMENT_RE.fullmatch(part)
        shaped.append(part if not capture else "{:path}" if capture.group(2) else "{}")
    return tuple(shaped)


# -- the fingerprint behind `stale` -------------------------------------------

def code_fingerprint(folders: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
    """Name, size and date of the modules of each folder (not recursive: a UI
    lists each folder whose modules its daemon runs; a recursive walk of a
    repository root would stat a whole virtualenv at every poll)."""
    found = []
    for folder in folders:
        for path in sorted(folder.glob("*.py")):
            try:
                info = path.stat()
            except OSError:
                continue
            found.append((str(path), info.st_size, info.st_mtime_ns))
    return tuple(found)


# -- static files, confined to a listing ----------------------------------------

def listing(root: Traversable | Path) -> dict[str, Traversable | Path]:
    """Every regular file under `root`, by its relative POSIX name. A request
    names one of these keys or gets a 404: no path is ever composed from what
    the request says, so `..`, an encoded slash or an absolute path cannot
    leave `root`. Symbolic links are left out of a folder on disk."""
    found: dict[str, Traversable | Path] = {}

    def walk(node: Traversable | Path, prefix: str) -> None:
        try:
            children = sorted(node.iterdir(), key=lambda child: child.name)
        except OSError:
            return
        for child in children:
            if isinstance(child, Path) and child.is_symlink():
                continue
            name = f"{prefix}{child.name}"
            if child.is_dir():
                if child.name != "__pycache__":
                    walk(child, name + "/")
            elif child.is_file():
                found[name] = child

    walk(root, "")
    return found


def plinth_static() -> Traversable:
    """plinth's own static folder, wherever the package was loaded from."""
    return resources.files("plinth") / "static"


# -- the app ----------------------------------------------------------------------

class App:
    """A UI's daemon: its name, its guard header, its port, its routes.

    `header` has no default, on purpose: a UI that forgets it does not start.
    `code_dirs` (keyword, required) are the folders whose modules the daemon
    runs; with plinth's own, they make `stale` (pass `()` to watch plinth only).
    """

    def __init__(self, name: str, header: str, port: int, *, code_dirs: Sequence[Path],
                 max_body: int = MAX_BODY) -> None:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ServerError(f"name {name!r}: lowercase letters, digits and '-', a letter first")
        if not isinstance(header, str) or not HEADER_RE.fullmatch(header):
            raise ServerError(f"header {header!r}: a custom header name starting with 'X-' "
                              f"(a CORS-safelisted header would not force a preflight)")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ServerError(f"port {port!r}: an integer between 0 and 65535")
        if isinstance(code_dirs, (str, Path)):
            raise ServerError("code_dirs is a sequence of folders, not one folder")
        if max_body < 0:
            raise ServerError("max_body must be positive")
        self.name = name
        self.header = header
        self.port = port
        self.max_body = max_body
        self.watched: tuple[Path, ...] = (CODE_DIR, *(Path(d).resolve() for d in code_dirs))
        self._code_at_start = code_fingerprint(self.watched)
        self._routes: list[_Route] = []
        self._errors: list[tuple[type[BaseException], HTTPStatus]] = []
        self._background: list[tuple[str, Callable[[threading.Event], None]]] = []
        self._server: Server | None = None
        self._add("GET", "/plinth/status", self._status)
        self._add("GET", "/plinth/static/{name}", self._plinth_static)

    # -- declaration ------------------------------------------------------------
    def route(self, method: str, pattern: str) -> Callable[[RouteHandler], RouteHandler]:
        """Decorator: `handler(req)` answers `method` on `pattern`.

        `{name}` captures one decoded segment, `{name:path}` the rest (last
        only). The first matching route wins, in declaration order. A handler
        returns a `Response`, None (204), or anything JSON can carry (200)."""
        method = method.upper()
        if method not in ROUTE_METHODS:
            raise ServerError(f"method {method!r}: one of {sorted(ROUTE_METHODS)} "
                              f"(HEAD follows GET)")
        if pattern == RESERVED or pattern.startswith(RESERVED + "/"):
            raise ServerError(f"route {pattern!r}: {RESERVED}/ is reserved to plinth")

        def register(handler: RouteHandler) -> RouteHandler:
            self._add(method, pattern, handler)
            return handler
        return register

    def _add(self, method: str, pattern: str, handler: RouteHandler) -> None:
        parts = _parse_pattern(pattern)
        if any(r.method == method and _shape(r.parts) == _shape(parts) for r in self._routes):
            raise ServerError(f"route {method} {pattern} declared twice (the first one "
                              f"would always win)")
        self._routes.append(_Route(method, pattern, parts, handler))

    def map_error(self, exc_type: type[BaseException], status: int) -> None:
        """An exception a route may raise, answered `status` with its message.
        Checked in declaration order; anything unmapped is a 500, logged."""
        self._errors.append((exc_type, HTTPStatus(status)))

    def add_static(self, prefix: str, root: Traversable | Path) -> None:
        """Serve the files listed under `root` at `prefix/<relative name>`."""
        prefix = "/" + prefix.strip("/")
        if prefix == "/" or prefix == RESERVED or prefix.startswith(RESERVED + "/"):
            raise ServerError(f"static prefix {prefix!r}: neither / nor under {RESERVED}/")
        if not root.is_dir():
            raise ServerError(f"static folder {root} does not exist")

        def serve_file(req: Request) -> Response:
            found = listing(root).get(req.params["name"])
            if found is None:
                raise Refused(HTTPStatus.NOT_FOUND, "no such file")
            return file_response(found)
        self._add("GET", prefix + "/{name:path}", serve_file)

    def background(self, name: str, target: Callable[[threading.Event], None]) -> None:
        """A thread `serve` starts with the daemon; it should return once the
        event it receives is set (the daemon is stopping)."""
        self._background.append((name, target))

    # -- state ------------------------------------------------------------------
    @property
    def stale(self) -> bool:
        """True once the code on disk is no longer the code this daemon runs."""
        return code_fingerprint(self.watched) != self._code_at_start

    def _status(self, req: Request) -> object:
        port = self._server.port if self._server else self.port
        return {"name": self.name, "stale": self.stale, "plinth": __version__, "port": port}

    def _plinth_static(self, req: Request) -> Response:
        found = listing(plinth_static()).get(req.params["name"])
        if found is None:
            raise Refused(HTTPStatus.NOT_FOUND, "no such file")
        return file_response(found)

    def _match(self, method: str, segments: tuple[str, ...]) -> tuple[_Route, dict[str, str]]:
        other_method = False
        for candidate in self._routes:
            params = candidate.match(segments)
            if params is None:
                continue
            if candidate.method == method:
                return candidate, params
            other_method = True
        if other_method:
            raise Refused(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} is not allowed here")
        raise Refused(HTTPStatus.NOT_FOUND, "not found")

    def _status_for(self, exc: BaseException) -> HTTPStatus | None:
        for exc_type, status in self._errors:
            if isinstance(exc, exc_type):
                return status
        return None

    # -- the server ---------------------------------------------------------------
    def make_server(self, port: int | None = None) -> Server:
        """Bind 127.0.0.1 (`port` overrides the App's, 0 picks a free one)."""
        chosen = self.port if port is None else port
        try:
            server = Server((LOOPBACK, chosen), self)
        except OSError as exc:
            raise ServerError(f"cannot listen on {LOOPBACK}:{chosen}: {exc}") from exc
        self._server = server
        return server


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App) -> None:
        super().__init__(address, _Handler)
        self.app = app

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def base_url(self) -> str:
        """The URL to hand out: the IPv4 loopback, never `localhost`."""
        return f"http://{LOOPBACK}:{self.port}"


class _Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), format % args)

    # -- writing ------------------------------------------------------------------
    def _write(self, response: Response, *, close: bool = False) -> None:
        self.send_response(response.status)
        if response.body or response.status not in (HTTPStatus.NO_CONTENT,
                                                    HTTPStatus.NOT_MODIFIED):
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in response.headers:
            self.send_header(name, value)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD" and response.body:
            self.wfile.write(response.body)

    # -- the guards -----------------------------------------------------------------
    def _check_host(self) -> None:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.port
        if host not in {f"localhost:{port}", f"{LOOPBACK}:{port}", f"[::1]:{port}"}:
            raise Refused(HTTPStatus.FORBIDDEN, f"unexpected Host {host!r}")

    def _check_client(self) -> None:
        header = self.server.app.header
        if self.headers.get(header) is None:
            raise Refused(HTTPStatus.FORBIDDEN, f"missing {header} header (cross-site request?)")
        origin = self.headers.get("Origin")
        port = self.server.port
        if origin is not None and origin.lower() not in {f"http://localhost:{port}",
                                                         f"http://{LOOPBACK}:{port}"}:
            raise Refused(HTTPStatus.FORBIDDEN, f"foreign Origin {origin!r}")

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise Refused(HTTPStatus.LENGTH_REQUIRED, "a body is sent with a Content-Length")
        raw = self.headers.get("Content-Length")
        if raw is None:
            return b""
        try:
            length = int(raw)
            if length < 0:
                raise ValueError(raw)
        except ValueError as exc:
            raise Refused(HTTPStatus.BAD_REQUEST, "bad Content-Length") from exc
        if length > self.server.app.max_body:
            raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
        return self.rfile.read(length) if length else b""

    # -- dispatch ---------------------------------------------------------------------
    def _handle(self, method: str) -> None:
        app = self.server.app
        try:
            self._check_host()
            if method not in SAFE_METHODS:
                self._check_client()
            body = self._read_body()
            split = urlsplit(self.path)
            segments = tuple(unquote(p) for p in split.path.split("/") if p)
            routed = "GET" if method == "HEAD" else method
            route, params = app._match(routed, segments)
            request = Request(routed, split.path, segments, params, parse_qs(split.query),
                              self.headers, body, app)
            result = route.handler(request)
        except Refused as exc:
            self._write(json_response({"error": str(exc)}, exc.status), close=True)
            return
        except Exception as exc:
            status = app._status_for(exc)
            if status is None:
                log.exception("%s %s", method, self.path)
                message = f"internal error ({type(exc).__name__}): see the daemon's log"
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            else:
                message = str(exc)
            self._write(json_response({"error": message}, status))
            return
        if isinstance(result, Response):
            self._write(result)
        elif result is None:
            self._write(Response(HTTPStatus.NO_CONTENT))
        else:
            self._write(json_response(result))

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802 - a CORS preflight: never granted
        self._handle("OPTIONS")


# -- serving ------------------------------------------------------------------

@dataclass
class _StopFlag:
    """Set by the signal handler. A plain attribute, no lock: a handler that
    takes a lock can deadlock when a second signal interrupts it."""

    requested: bool = False
    threads: list[threading.Thread] = field(default_factory=list)


def serve(app: App, *, port: int | None = None,
          on_ready: Callable[[str], None] | None = None) -> int:
    """Run the daemon until SIGTERM or SIGINT; return 0. Call it from the main
    thread (signal handlers can only be set there). `on_ready` receives the
    URL once the socket listens; the background threads get an event set at
    shutdown."""
    srv = app.make_server(port)
    flag = _StopFlag()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        flag.requested = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    worker = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    worker.start()
    stopping = threading.Event()
    for name, target in app._background:
        thread = threading.Thread(target=target, args=(stopping,), name=name, daemon=True)
        thread.start()
        flag.threads.append(thread)
    log.info("%s daemon listening on %s", app.name, srv.base_url())
    if on_ready:
        on_ready(srv.base_url())
    while not flag.requested:
        time.sleep(0.2)
    # From here on, a late signal must not kill the interpreter while it
    # finalises.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    stopping.set()
    srv.shutdown()
    srv.server_close()
    for thread in flag.threads:
        thread.join(timeout=5)
    log.info("%s daemon stopped", app.name)
    return 0
