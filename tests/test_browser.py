"""The example UI in a real browser (headless Chromium through Playwright).

Excluded from the default run: it needs Node and a Playwright install the
repository does not ship. Run it with

    PLINTH_PLAYWRIGHT_DIR=<dir whose node_modules holds playwright> \\
        uv run pytest -m browser -q

Without that variable it FAILS rather than skips (run_browser_check).
`PLINTH_BROWSER_OUT=<dir>` keeps the screenshots, to look at them.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from hello import app as hello

from plinth import server, terminal
from plinth.testing import run_browser_check

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve().parent / "browser" / "hello_check.mjs"


@pytest.mark.browser
def test_the_example_ui_in_a_real_browser(tmp_path, monkeypatch):
    # The daemon watches a COPY of plinth's modules: the script changes one,
    # and the page must say the daemon is stale.
    copy = tmp_path / "plinth-copy"
    copy.mkdir()
    for module in (ROOT / "src" / "plinth").glob("*.py"):
        (copy / module.name).write_bytes(module.read_bytes())
    monkeypatch.setattr(server, "CODE_DIR", copy)
    # The terminal route runs a fake Windows Terminal that records its call.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "wt-argv"
    fake = bin_dir / "fake-wt"
    fake.write_text(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {record}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(terminal, "WT", "fake-wt")
    monkeypatch.setenv("PLINTH_PLATFORM", "wsl")
    monkeypatch.setenv("WSL_DISTRO_NAME", "TestDistro")

    out = run_browser_check(hello.build(port=0), SCRIPT, str(copy / "server.py"),
                            out_dir=tmp_path / "shots")
    print(out)
    assert out.rstrip().endswith("DONE")
    assert out.count("OK   ") >= 14
    argv = record.read_text().splitlines()
    assert argv[:5] == ["-w", "0", "new-tab", "--title", "hello-i2"]
