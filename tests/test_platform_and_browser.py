"""Which machine this is, and which program opens a URL there."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from plinth import browser, platform
from plinth.browser import OpenError


def test_the_override_wins(monkeypatch):
    for kind in ("wsl", "linux", "macos", "other"):
        monkeypatch.setenv("PLINTH_PLATFORM", kind)
        assert platform.platform() == kind


def test_wsl_is_detected_by_its_variable_or_the_kernel(monkeypatch):
    monkeypatch.delenv("PLINTH_PLATFORM", raising=False)
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Distro")
    assert platform.platform() == "wsl"
    monkeypatch.delenv("WSL_DISTRO_NAME")
    monkeypatch.setattr(platform, "kernel_release", lambda: "6.6.0-microsoft-standard-WSL2")
    assert platform.platform() == "wsl"
    monkeypatch.setattr(platform, "kernel_release", lambda: "6.8.0-generic")
    assert platform.platform() == "linux"
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    assert platform.platform() == "macos"
    monkeypatch.setattr(platform.sys, "platform", "win32")
    assert platform.platform() == "other"


def test_the_distribution_comes_from_its_variable_or_wslpath(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Named")
    assert platform.wsl_distro() == "Named"
    monkeypatch.delenv("WSL_DISTRO_NAME")

    def fake_run(argv, **kwargs):
        assert argv == ["wslpath", "-w", "/"]
        return subprocess.CompletedProcess(argv, 0, "\\\\wsl.localhost\\FromShare\\\n", "")
    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    assert platform.wsl_distro() == "FromShare"
    monkeypatch.setattr(platform.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "no"))
    with pytest.raises(platform.PlatformError):
        platform.wsl_distro()


def _which(found: dict[str, str]):
    return lambda name: found.get(name)


def test_the_openers_of_each_platform(monkeypatch):
    found = {"xdg-open": "/bin/xdg-open", "open": "/usr/bin/open", "wslview": "/bin/wslview",
             "cmd.exe": "/mnt/c/cmd.exe", "explorer.exe": "/mnt/c/explorer.exe"}
    monkeypatch.setattr(shutil, "which", _which(found))
    monkeypatch.setenv("PLINTH_PLATFORM", "linux")
    assert browser.openers("http://127.0.0.1:1/") == [["/bin/xdg-open", "http://127.0.0.1:1/"]]
    monkeypatch.setenv("PLINTH_PLATFORM", "macos")
    assert browser.openers("u") == [["/usr/bin/open", "u"]]
    monkeypatch.setenv("PLINTH_PLATFORM", "wsl")
    assert browser.openers("u") == [["/bin/wslview", "u"], ["/mnt/c/cmd.exe", "/c", "start", "", "u"],
                                    ["/mnt/c/explorer.exe", "u"]]
    monkeypatch.setenv("PLINTH_PLATFORM", "other")
    assert browser.openers("u") == []


def _fake(folder: Path, name: str, code: int, log: Path) -> str:
    path = folder / name
    path.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit {code}\n")
    path.chmod(0o755)
    return str(path)


def test_open_url_tries_each_opener_until_one_works(monkeypatch, tmp_path):
    log = tmp_path / "log"
    failing = _fake(tmp_path, "fake-failing", 3, log)
    working = _fake(tmp_path, "fake-working", 0, log)
    monkeypatch.setattr(browser, "openers", lambda url: [[failing, url], [working, url]])
    assert browser.open_url("http://127.0.0.1:9/") == "fake-working"
    assert log.read_text().splitlines() == ["http://127.0.0.1:9/", "http://127.0.0.1:9/"]


def test_an_opener_whose_exit_code_lies_counts_as_success_once_launched(monkeypatch, tmp_path):
    odd = _fake(tmp_path, "fake-explorer", 1, tmp_path / "log")
    monkeypatch.setattr(browser, "openers", lambda url: [[odd, url]])
    with pytest.raises(OpenError, match="exited 1"):
        browser.open_url("u")
    monkeypatch.setattr(browser, "EXIT_CODE_IGNORED", frozenset({"fake-explorer"}))
    assert browser.open_url("u") == "fake-explorer"


def test_no_opener_is_said_with_the_url_to_open_by_hand(monkeypatch):
    monkeypatch.setattr(browser, "openers", lambda url: [])
    monkeypatch.setenv("PLINTH_PLATFORM", "linux")
    with pytest.raises(OpenError, match="by hand: http://127.0.0.1:3/"):
        browser.open_url("http://127.0.0.1:3/")
