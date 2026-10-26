"""Talking to a running daemon from Python (a command line, a hook, a test).

The guard header is a required argument, as it is for `App`: a call without
it would be refused by the daemon on any write, and that refusal would read
like a bug of the daemon.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .server import LOOPBACK

START_WAIT = 8.0
PROBE = "/plinth/status"


class ClientError(Exception):
    """The daemon answered with an error (`status` holds the HTTP code)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class DaemonUnreachable(ClientError):
    """Nothing answers on that port."""


def call(port: int, method: str, path: str, *, header: str, body: object = None,
         timeout: float = 10) -> dict[str, Any]:
    """`method path` on the daemon of `port`, a JSON body if `body` is not None.
    Return the JSON answer (a non-object answer comes back as {"value": …})."""
    url = f"http://{LOOPBACK}:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method.upper())
    request.add_header(header, "client")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error", "")
        except (ValueError, AttributeError):
            message = ""
        raise ClientError(f"{method} {path}: HTTP {exc.code} {message}".strip(),
                          exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DaemonUnreachable(f"no daemon on {LOOPBACK}:{port} ({exc})") from exc
    try:
        result = json.loads(payload) if payload else {}
    except json.JSONDecodeError as exc:
        raise ClientError(f"{method} {path}: unreadable answer") from exc
    return result if isinstance(result, dict) else {"value": result}


def ensure_daemon(port: int, start: Callable[[], str], *, header: str,
                  probe: str = PROBE, wait: float = START_WAIT) -> str:
    """`"running"` if a daemon answers `probe`, else start one with `start`
    (which returns how it started it) and wait until it answers. Raise
    DaemonUnreachable if it never does: the caller says so instead of waiting
    in silence."""
    try:
        call(port, "GET", probe, header=header, timeout=2)
        return "running"
    except DaemonUnreachable:
        pass
    how = start()
    deadline = time.monotonic() + wait
    while True:
        try:
            call(port, "GET", probe, header=header, timeout=2)
            return f"started ({how})"
        except DaemonUnreachable:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
