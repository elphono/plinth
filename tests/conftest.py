"""plinth's own suite runs under the guards it offers to every UI.

- no test launches a program plinth itself may launch (PLATFORM_PROGRAMS:
  wt.exe, cmd.exe, wslview, xdg-open, systemctl...) nor sends a real signal
  (tests/test_testing.py is their witness);
- the example UI `hello` reads its config and writes its data under tmp_path.
"""
from __future__ import annotations

import http.client
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plinth.server import App, Server
from plinth.testing import isolate, make_guards

_guards = make_guards(forbidden=())
_isolated_hello = isolate("hello")

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        import json
        return json.loads(self.body.decode("utf-8"))


@dataclass
class Daemon:
    app: App
    srv: Server

    @property
    def port(self) -> int:
        return self.srv.port

    def request(self, method: str, path: str, body: bytes | str | None = None,
                headers: dict[str, str] | None = None, *, guard: bool = True) -> Answer:
        """A request with a local Host, and the guard header unless `guard` is
        False (headers given here win)."""
        sent = {"Host": f"127.0.0.1:{self.port}"}
        if guard:
            sent[self.app.header] = "test"
        sent.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=sent)
            response = conn.getresponse()
            return Answer(response.status, {k.lower(): v for k, v in response.getheaders()},
                          response.read())
        finally:
            conn.close()


ServeApp = Callable[[App], "Daemon"]


@pytest.fixture
def serve_app() -> Iterator[ServeApp]:
    """serve_app(app) -> a Daemon serving `app` on a free port, in a thread."""
    started: list[Server] = []

    def start(app: App) -> Daemon:
        srv = app.make_server(port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return Daemon(app, srv)

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()
