"""The example UI, hello, over HTTP: what its page relies on."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import Daemon, ServeApp
from hello import app as hello

from plinth import terminal


@pytest.fixture
def daemon(serve_app: ServeApp) -> Daemon:
    return serve_app(hello.build(port=0))


def test_the_page_and_both_kinds_of_static_files_are_served(daemon: Daemon):
    page = daemon.request("GET", "/")
    assert page.status == 200 and page.headers["content-type"].startswith("text/html")
    assert b'href="/plinth/static/ihm.css"' in page.body
    assert b'src="/static/hello/hello.js"' in page.body
    for path in ("/plinth/static/ihm.css", "/plinth/static/ihm.js", "/static/hello/hello.js",
                 "/static/hello/hello.css"):
        assert daemon.request("GET", path).status == 200, path
    assert daemon.request("GET", "/static/hello/../app.py").status == 404


def test_items_are_listed_added_toned_and_deleted(daemon: Daemon):
    listing = daemon.request("GET", "/api/items").json()
    assert isinstance(listing, dict)
    assert [i["name"] for i in listing["items"]] == ["Alpha", "Bravo", "Charlie"]
    assert listing["tones"] == list(hello.TONES)
    added = daemon.request("POST", "/api/items", b'{"name": "Delta"}').json()
    assert isinstance(added, dict) and added["message"] == "Added Delta."
    item = added["item"]["id"]
    toned = daemon.request("POST", f"/api/items/{item}/tone", b'{"tone": "live"}').json()
    assert isinstance(toned, dict) and toned["item"]["tone"] == "live"
    assert daemon.request("POST", f"/api/items/{item}/tone", b'{"tone": "pink"}').status == 400
    assert daemon.request("DELETE", f"/api/items/{item}").json() == {"message": "Deleted Delta."}
    assert daemon.request("DELETE", f"/api/items/{item}").status == 404


def test_a_locked_item_is_refused_with_its_reason(daemon: Daemon):
    answer = daemon.request("DELETE", "/api/items/i3")
    assert answer.status == 409
    assert answer.json() == {"error": "Charlie is locked: it cannot be deleted."}


def test_an_empty_name_is_refused(daemon: Daemon):
    assert daemon.request("POST", "/api/items", b'{"name": "  "}').status == 400
    assert daemon.request("POST", "/api/items", b'"Delta"').status == 400


def test_the_page_cannot_write_without_the_guard_header(daemon: Daemon):
    assert daemon.request("POST", "/api/items", b'{"name": "x"}', guard=False).status == 403
    assert daemon.request("GET", "/api/items").json()["items"][-1]["name"] == "Charlie"


def test_the_terminal_route_greets_the_item_in_a_tab(daemon: Daemon, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv"
    fake = bin_dir / "fake-wt"
    fake.write_text(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {record}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(terminal, "WT", "fake-wt")
    monkeypatch.setenv("PLINTH_PLATFORM", "wsl")
    monkeypatch.setenv("WSL_DISTRO_NAME", "TestDistro")
    answer = daemon.request("POST", "/api/items/i1/terminal")
    assert answer.json() == {"message": "A terminal tab greets Alpha (fake-wt)."}
    argv = record.read_text().splitlines()
    assert argv[:5] == ["-w", "0", "new-tab", "--title", "hello-i1"]
    script = Path(argv[-1])
    assert script.is_relative_to(tmp_path)            # the isolated data folder of hello
    ran = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30,
                         stdin=subprocess.DEVNULL, env={**os.environ, "HOME": str(tmp_path)})
    assert ran.stdout.startswith("Hello, Alpha!\n")
    assert "finished, exit code 0" in ran.stdout     # keep_open: the tab stays


def test_the_port_comes_from_the_config(monkeypatch):
    monkeypatch.setenv("HELLO_PORT", "8899")
    assert hello.build().port == 8899
    assert hello.build(port=0).port == 0
