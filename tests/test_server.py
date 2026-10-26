"""The daemon: its guards come before any route, it listens on the loopback
only, it serves a static file only if it is listed, and it says when its code
changed on disk."""
from __future__ import annotations

import inspect
import os
import signal
import socket
import subprocess
import sys
import textwrap
from http import HTTPStatus
from pathlib import Path

import pytest
from conftest import ROOT, Daemon, ServeApp

from plinth import server
from plinth.server import App, Refused, Request, Response, ServerError

STATIC = ROOT / "src" / "plinth" / "static"


def recording_app(tmp_path: Path, **kwargs: object) -> tuple[App, list[str]]:
    """An app whose routes record that they ran: a guard must refuse BEFORE."""
    app = App("probe", header="X-Probe", port=0, code_dirs=[tmp_path], **kwargs)  # type: ignore[arg-type]
    calls: list[str] = []

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        def handler(req: Request, method: str = method) -> object:
            calls.append(method)
            return {"method": req.method, "body": req.body.decode()}
        app.route(method, "/api/thing")(handler)
    return app, calls


# -- the socket ------------------------------------------------------------------

def _outside_address() -> str | None:
    """An IPv4 address of this machine that is not the loopback, if any (no
    packet is sent: a UDP connect only chooses a route)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))            # TEST-NET-1, never routed for real
        address = str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") else address


def test_the_socket_listens_on_the_ipv4_loopback_only(tmp_path, serve_app: ServeApp):
    app, _ = recording_app(tmp_path)
    daemon = serve_app(app)
    assert daemon.srv.server_address[0] == "127.0.0.1"
    assert daemon.srv.base_url() == f"http://127.0.0.1:{daemon.port}"
    # Nothing lets a UI choose another address.
    assert "host" not in inspect.signature(App.make_server).parameters
    assert "host" not in inspect.signature(App).parameters
    outside = _outside_address()
    if outside:
        with pytest.raises(OSError):
            socket.create_connection((outside, daemon.port), timeout=2).close()
    assert daemon.request("GET", "/plinth/status").status == 200


# -- the App's arguments -------------------------------------------------------------

def test_the_guard_header_is_required_and_has_no_default():
    with pytest.raises(TypeError):
        App("hello", port=0, code_dirs=())  # type: ignore[call-arg]
    parameter = inspect.signature(App).parameters["header"]
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize("header", ["", "Content-Type", "Accept", "X-", "X-a b", "X-a;b", 42])
def test_the_guard_header_must_be_a_custom_x_header(header):
    with pytest.raises(ServerError, match="header"):
        App("hello", header, 0, code_dirs=())


@pytest.mark.parametrize("name", ["", "Hello", "1ui", "a b", "ui_x"])
def test_the_name_is_a_plain_lowercase_word(name):
    with pytest.raises(ServerError, match="name"):
        App(name, "X-Ui", 0, code_dirs=())


@pytest.mark.parametrize("port", [-1, 65536, "8770", True, 1.5])
def test_the_port_is_an_integer_in_range(port):
    with pytest.raises(ServerError, match="port"):
        App("ui", "X-Ui", port, code_dirs=())


def test_code_dirs_is_required_and_is_a_sequence(tmp_path):
    with pytest.raises(TypeError):
        App("ui", "X-Ui", 0)  # type: ignore[call-arg]
    with pytest.raises(ServerError, match="sequence"):
        App("ui", "X-Ui", 0, code_dirs=tmp_path)


# -- the guards, before any route --------------------------------------------------------

def test_a_foreign_host_is_refused_on_every_method_before_any_route(tmp_path, serve_app):
    app, calls = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    for host in ("evil.example", f"evil.example:{daemon.port}", "127.0.0.1:1",
                 f"127.0.0.1.evil.example:{daemon.port}", ""):
        for method in ("GET", "POST", "DELETE"):
            answer = daemon.request(method, "/api/thing", b"{}", {"Host": host})
            assert answer.status == 403, (host, method)
            assert b"unexpected Host" in answer.body
        assert daemon.request("GET", "/plinth/status", headers={"Host": host}).status == 403
    assert calls == []
    # The witness: the same requests with a local Host reach the route.
    for host in (f"127.0.0.1:{daemon.port}", f"localhost:{daemon.port}", f"[::1]:{daemon.port}"):
        assert daemon.request("GET", "/api/thing", headers={"Host": host}).status == 200
    assert calls == ["GET", "GET", "GET"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_write_without_the_guard_header_is_refused_before_the_route(tmp_path, serve_app,
                                                                      method):
    app, calls = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    answer = daemon.request(method, "/api/thing", b"{}", guard=False)
    assert answer.status == 403
    assert b"missing X-Probe header" in answer.body
    assert calls == []
    # Another header name is not the guard header.
    answer = daemon.request(method, "/api/thing", b"{}", {"X-Other": "1"}, guard=False)
    assert answer.status == 403 and calls == []
    # The witness: with the header, the route runs.
    assert daemon.request(method, "/api/thing", b"{}").status == 200
    assert calls == [method]


def test_a_read_needs_no_guard_header(tmp_path, serve_app):
    app, calls = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    assert daemon.request("GET", "/api/thing", guard=False).status == 200
    assert calls == ["GET"]


def test_a_write_from_a_foreign_origin_is_refused(tmp_path, serve_app):
    app, calls = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    for origin in ("https://evil.example", "null", f"http://127.0.0.1:{daemon.port + 1}",
                   f"https://127.0.0.1:{daemon.port}"):
        answer = daemon.request("POST", "/api/thing", b"{}", {"Origin": origin})
        assert answer.status == 403, origin
        assert b"foreign Origin" in answer.body
    assert calls == []
    for origin in (f"http://127.0.0.1:{daemon.port}", f"http://localhost:{daemon.port}"):
        assert daemon.request("POST", "/api/thing", b"{}", {"Origin": origin}).status == 200
    assert calls == ["POST", "POST"]


def test_a_cors_preflight_is_never_granted(tmp_path, serve_app):
    app, _ = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    answer = daemon.request("OPTIONS", "/api/thing", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-probe"}, guard=False)
    assert answer.status == 403
    assert not [h for h in answer.headers if h.startswith("access-control-")]


def test_the_body_is_bounded_and_never_chunked(tmp_path, serve_app):
    app, calls = recording_app(tmp_path, max_body=10)
    daemon: Daemon = serve_app(app)
    assert daemon.request("POST", "/api/thing", b"x" * 11).status == 413
    assert daemon.request("POST", "/api/thing", b"x" * 10).status == 200
    assert calls == ["POST"]
    answer = daemon.request("POST", "/api/thing", headers={"Transfer-Encoding": "chunked"})
    assert answer.status == 411
    assert daemon.request("POST", "/api/thing", headers={"Content-Length": "-4"}).status == 400
    assert calls == ["POST"]


def test_a_refusal_closes_the_connection_so_its_unread_body_is_never_a_request(tmp_path,
                                                                              serve_app):
    app, calls = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    host = f"127.0.0.1:{daemon.port}"
    # The body of the refused POST is itself a well-formed request.
    smuggled = (f"GET /api/thing HTTP/1.1\r\nHost: {host}\r\n\r\n").encode()
    head = (f"POST /api/thing HTTP/1.1\r\nHost: {host}\r\n"
            f"Content-Length: {len(smuggled)}\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", daemon.port), timeout=5) as conn:
        conn.sendall(head + smuggled)
        received = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break                       # the server closed the connection
            received += chunk
    assert received.count(b"HTTP/1.1 ") == 1, received
    assert received.startswith(b"HTTP/1.1 403")
    assert b"connection: close" in received.lower()
    assert calls == []


def test_every_answer_is_never_cached_never_sniffed_never_framed(tmp_path, serve_app):
    app, _ = recording_app(tmp_path)
    daemon: Daemon = serve_app(app)
    answers = [daemon.request("GET", "/api/thing"), daemon.request("GET", "/nowhere"),
               daemon.request("POST", "/api/thing", guard=False),
               daemon.request("GET", "/plinth/static/ihm.css")]
    assert [a.status for a in answers] == [200, 404, 403, 200]
    for answer in answers:
        assert answer.headers["cache-control"] == "no-store"
        assert answer.headers["x-content-type-options"] == "nosniff"
        assert answer.headers["x-frame-options"] == "DENY"
        assert answer.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_a_route_cannot_replace_a_header_the_app_sets():
    with pytest.raises(ServerError, match="Cache-Control"):
        Response(200, b"", "text/plain", (("Cache-Control", "max-age=60"),))


# -- routes ---------------------------------------------------------------------------------

def test_routes_capture_segments_and_answer_by_what_they_return(tmp_path, serve_app):
    app = App("ui", "X-Ui", 0, code_dirs=[tmp_path])

    class Locked(Exception):
        pass

    app.map_error(KeyError, 404)
    app.map_error(Locked, 409)

    @app.route("GET", "/api/items/{item}")
    def item(req: Request) -> object:
        return {"item": req.params["item"], "q": req.arg("q", "none")}

    @app.route("GET", "/files/{rest:path}")
    def rest(req: Request) -> object:
        return {"rest": req.params["rest"]}

    @app.route("POST", "/api/items/{item}")
    def post(req: Request) -> object:
        return {"body": req.json_object()}

    @app.route("DELETE", "/api/items/{item}")
    def delete(req: Request) -> None:
        if req.params["item"] == "gone":
            raise KeyError("no item 'gone'")
        if req.params["item"] == "locked":
            raise Locked("locked: unlock it first")
        if req.params["item"] == "refused":
            raise Refused(400, "refused on purpose")
        if req.params["item"] == "bug":
            raise RuntimeError("secret detail")
        return None

    @app.route("GET", "/page")
    def page(req: Request) -> Response:
        return Response(201, b"<p>hi</p>", "text/html; charset=utf-8", (("X-Extra", "1"),))

    daemon: Daemon = serve_app(app)
    assert daemon.request("GET", "/api/items/a%2Fb?q=1").json() == {"item": "a/b", "q": "1"}
    assert daemon.request("GET", "/files/x/y%20z").json() == {"rest": "x/y z"}
    assert daemon.request("GET", "/files").status == 404
    assert daemon.request("POST", "/api/items/a", b'{"k": 1}').json() == {"body": {"k": 1}}
    assert daemon.request("POST", "/api/items/a", b"[1]").status == 400
    assert daemon.request("POST", "/api/items/a", b"{nope").status == 400
    assert daemon.request("PUT", "/api/items/a", b"{}").status == 405
    assert daemon.request("GET", "/api/nothing").status == 404
    assert daemon.request("DELETE", "/api/items/ok").status == 204
    gone = daemon.request("DELETE", "/api/items/gone")
    assert gone.status == 404 and "gone" in str(gone.json())
    locked = daemon.request("DELETE", "/api/items/locked")
    assert locked.status == 409 and locked.json() == {"error": "locked: unlock it first"}
    assert daemon.request("DELETE", "/api/items/refused").status == 400
    bug = daemon.request("DELETE", "/api/items/bug")
    assert bug.status == 500 and b"secret detail" not in bug.body
    page_answer = daemon.request("GET", "/page")
    assert page_answer.status == 201 and page_answer.body == b"<p>hi</p>"
    assert page_answer.headers["x-extra"] == "1"
    head = daemon.request("HEAD", "/page")
    assert head.status == 201 and head.body == b""


@pytest.mark.parametrize("method,pattern,why", [
    ("GET", "/plinth/mine", "reserved"),
    ("GET", "/plinth", "reserved"),
    ("TRACE", "/x", "method"),
    ("HEAD", "/x", "method"),
    ("GET", "x", "starts with"),
    ("GET", "/x/{a:path}/y", "must come last"),
    ("GET", "/x/{a}/{a}", "twice"),
    ("GET", "/x/{a b}", "is not"),
])
def test_a_bad_route_is_refused_at_declaration(method, pattern, why):
    app = App("ui", "X-Ui", 0, code_dirs=())
    with pytest.raises(ServerError, match=why):
        app.route(method, pattern)(lambda req: None)


def test_a_route_declared_twice_is_refused():
    app = App("ui", "X-Ui", 0, code_dirs=())
    app.route("GET", "/x/{a}")(lambda req: None)
    with pytest.raises(ServerError, match="twice"):
        app.route("GET", "/x/{b}")(lambda req: None)


# -- static files -----------------------------------------------------------------------------

def test_plinth_serves_its_own_static_files(tmp_path, serve_app):
    daemon: Daemon = serve_app(App("ui", "X-Ui", 0, code_dirs=()))
    css = daemon.request("GET", "/plinth/static/ihm.css")
    assert css.status == 200 and css.headers["content-type"].startswith("text/css")
    assert css.body == (STATIC / "ihm.css").read_bytes()
    js = daemon.request("GET", "/plinth/static/ihm.js")
    assert js.status == 200 and "javascript" in js.headers["content-type"]
    assert js.body == (STATIC / "ihm.js").read_bytes()
    assert daemon.request("GET", "/plinth/static/nothing.css").status == 404


@pytest.mark.parametrize("path", [
    "/plinth/static/..%2F__init__.py", "/plinth/static/..%2Fserver.py",
    "/plinth/static/%2E%2E%2Fserver.py", "/plinth/static/..", "/plinth/static/.",
    "/plinth/static/%2Fetc%2Fpasswd",
])
def test_plinth_static_never_serves_outside_its_listing(serve_app, path):
    daemon: Daemon = serve_app(App("ui", "X-Ui", 0, code_dirs=()))
    answer = daemon.request("GET", path)
    assert answer.status == 404, (path, answer.body[:80])
    assert b"import" not in answer.body and b"root:" not in answer.body


@pytest.mark.parametrize("path", [
    "/static/ui/../secret.py", "/static/ui/..%2Fsecret.py", "/static/ui/sub/../../secret.py",
    "/static/ui/%2Fetc%2Fpasswd", "/static/ui//etc/passwd", "/static/ui/link.txt",
    "/static/ui/sub", "/static/ui/%2E%2E/secret.py",
])
def test_a_ui_static_folder_never_serves_outside_its_listing(tmp_path, serve_app, path):
    folder = tmp_path / "static"
    (folder / "sub").mkdir(parents=True)
    (folder / "page.css").write_text("body {}")
    (folder / "sub" / "deep.js").write_text("// deep")
    (tmp_path / "secret.py").write_text("SECRET = 'import me'")
    (folder / "link.txt").symlink_to(tmp_path / "secret.py")
    app = App("ui", "X-Ui", 0, code_dirs=())
    app.add_static("/static/ui", folder)
    daemon: Daemon = serve_app(app)
    # The witness: listed files are served.
    assert daemon.request("GET", "/static/ui/page.css").body == b"body {}"
    assert daemon.request("GET", "/static/ui/sub/deep.js").body == b"// deep"
    answer = daemon.request("GET", path)
    assert answer.status == 404, (path, answer.body[:80])
    assert b"SECRET" not in answer.body and b"root:" not in answer.body


def test_a_static_folder_is_read_at_each_request(tmp_path, serve_app):
    folder = tmp_path / "static"
    folder.mkdir()
    app = App("ui", "X-Ui", 0, code_dirs=())
    app.add_static("/static/ui", folder)
    daemon: Daemon = serve_app(app)
    assert daemon.request("GET", "/static/ui/new.js").status == 404
    (folder / "new.js").write_text("// new")
    assert daemon.request("GET", "/static/ui/new.js").body == b"// new"


@pytest.mark.parametrize("prefix", ["/", "/plinth", "/plinth/static"])
def test_a_static_prefix_cannot_take_the_root_or_plinth(tmp_path, prefix):
    with pytest.raises(ServerError):
        App("ui", "X-Ui", 0, code_dirs=()).add_static(prefix, tmp_path)


# -- stale -------------------------------------------------------------------------------------

def test_the_daemon_turns_stale_when_a_module_of_the_ui_changes(tmp_path, serve_app):
    code = tmp_path / "ui"
    code.mkdir()
    module = code / "routes.py"
    module.write_text("A = 1\n")
    daemon: Daemon = serve_app(App("ui", "X-Ui", 0, code_dirs=[code]))
    assert daemon.request("GET", "/plinth/status").json() == {
        "name": "ui", "stale": False, "plinth": server.__version__, "port": daemon.port}
    module.write_text("A = 22\n")
    assert daemon.app.stale
    assert daemon.request("GET", "/plinth/status").json()["stale"] is True


def test_the_daemon_turns_stale_when_a_module_of_plinth_changes(tmp_path, monkeypatch):
    """The lib is part of the fingerprint: an update of plinth alone changes
    what runs. Checked on a copy of plinth's modules, never on the real ones."""
    copy = tmp_path / "plinth-copy"
    copy.mkdir()
    for module in (ROOT / "src" / "plinth").glob("*.py"):
        (copy / module.name).write_bytes(module.read_bytes())
    monkeypatch.setattr(server, "CODE_DIR", copy)
    ui = tmp_path / "ui"
    ui.mkdir()
    app = App("ui", "X-Ui", 0, code_dirs=[ui])
    assert copy in app.watched
    assert not app.stale
    with (copy / "server.py").open("a") as handle:
        handle.write("\n# a newer plinth\n")
    assert app.stale


def test_a_new_module_in_a_watched_folder_makes_it_stale(tmp_path):
    app = App("ui", "X-Ui", 0, code_dirs=[tmp_path])
    assert not app.stale
    (tmp_path / "added.py").write_text("")
    assert app.stale


# -- serve ----------------------------------------------------------------------------------------

SERVE_SCRIPT = textwrap.dedent("""
    import sys, threading
    from pathlib import Path
    from plinth import App, serve

    done = Path(sys.argv[1])
    app = App("ui", "X-Ui", 0, code_dirs=())

    def worker(stop: threading.Event) -> None:
        stop.wait()
        done.write_text("stopped")

    app.background("worker", worker)
    raise SystemExit(serve(app, on_ready=lambda url: print(url, flush=True)))
""")


def test_serve_runs_until_sigterm_and_stops_its_background_threads(tmp_path):
    done = tmp_path / "done"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, "-c", SERVE_SCRIPT, str(done)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        assert proc.stdout is not None
        url = proc.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:"), url
        port = int(url.rsplit(":", 1)[1])
        conn = socket.create_connection(("127.0.0.1", port), timeout=5)
        conn.sendall(f"GET /plinth/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                     f"Connection: close\r\n\r\n".encode())
        assert conn.recv(100).startswith(b"HTTP/1.1 200")
        conn.close()
        assert not done.exists()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert done.read_text() == "stopped"


def test_a_port_already_taken_is_a_server_error(tmp_path):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    try:
        app = App("ui", "X-Ui", taken.getsockname()[1], code_dirs=())
        with pytest.raises(ServerError, match="cannot listen"):
            app.make_server()
    finally:
        taken.close()


def test_request_helpers_on_their_own():
    req = Request("POST", "/x", ("x",), {}, {"q": ["1", "2"]}, None, b"", None)  # type: ignore[arg-type]
    assert req.json() is None and req.json_object() == {} and req.arg("q") == "1"
    assert req.arg("missing", "d") == "d"
    assert HTTPStatus(Refused(404, "x").status) == 404
