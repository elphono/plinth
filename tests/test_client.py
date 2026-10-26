"""The Python client: the guard header goes with every call, errors keep their
status, and a daemon is started on demand only when none answers."""
from __future__ import annotations

import socket
import threading

import pytest
from conftest import Daemon, ServeApp

from plinth import client
from plinth.server import App, Refused, Request


def _app() -> App:
    app = App("ui", "X-Ui", 0, code_dirs=())

    @app.route("POST", "/api/echo")
    def echo(req: Request) -> object:
        return {"got": req.json(), "header": req.headers.get("X-Ui")}

    @app.route("GET", "/api/list")
    def listing(req: Request) -> object:
        return [1, 2]

    @app.route("DELETE", "/api/locked")
    def locked(req: Request) -> object:
        raise Refused(409, "it is locked")

    return app


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_call_carries_the_guard_header_and_its_body(serve_app: ServeApp):
    daemon: Daemon = serve_app(_app())
    answer = client.call(daemon.port, "POST", "/api/echo", header="X-Ui", body={"a": 1})
    assert answer == {"got": {"a": 1}, "header": "client"}
    assert client.call(daemon.port, "GET", "/api/list", header="X-Ui") == {"value": [1, 2]}


def test_a_call_with_another_header_is_refused_by_the_daemon(serve_app: ServeApp):
    daemon: Daemon = serve_app(_app())
    with pytest.raises(client.ClientError) as caught:
        client.call(daemon.port, "POST", "/api/echo", header="X-Other", body={})
    assert caught.value.status == 403


def test_an_error_keeps_its_status_and_message(serve_app: ServeApp):
    daemon: Daemon = serve_app(_app())
    with pytest.raises(client.ClientError, match="HTTP 409 it is locked") as caught:
        client.call(daemon.port, "DELETE", "/api/locked", header="X-Ui")
    assert caught.value.status == 409
    assert not isinstance(caught.value, client.DaemonUnreachable)


def test_nothing_listening_is_daemon_unreachable():
    with pytest.raises(client.DaemonUnreachable):
        client.call(_free_port(), "GET", "/plinth/status", header="X-Ui", timeout=2)


def test_the_header_is_a_required_argument():
    with pytest.raises(TypeError):
        client.call(1, "GET", "/")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.ensure_daemon(1, lambda: "x")  # type: ignore[call-arg]


def test_a_running_daemon_is_never_started_again(serve_app: ServeApp):
    daemon: Daemon = serve_app(_app())
    starts: list[str] = []

    def start() -> str:
        starts.append("x")
        return "x"
    assert client.ensure_daemon(daemon.port, start, header="X-Ui") == "running"
    assert starts == []


def test_a_missing_daemon_is_started_and_waited_for():
    port = _free_port()
    app = App("ui", "X-Ui", port, code_dirs=())
    servers = []

    def start() -> str:
        srv = app.make_server()
        servers.append(srv)
        threading.Timer(0.3, srv.serve_forever).start()
        return "in a thread"

    try:
        assert client.ensure_daemon(port, start, header="X-Ui") == "started (in a thread)"
        assert len(servers) == 1
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()


def test_a_daemon_that_never_answers_is_said_not_waited_for_forever():
    starts: list[int] = []

    def start() -> str:
        starts.append(1)
        return "nothing"
    with pytest.raises(client.DaemonUnreachable):
        client.ensure_daemon(_free_port(), start, header="X-Ui", wait=0.5)
    assert starts == [1]
