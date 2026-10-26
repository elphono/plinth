"""Test tooling for a UI built on plinth (imports pytest: test suites only).

**Guards every test runs under** — autouse fixtures a UI installs in its
`conftest.py`, with the programs no test of it may launch:

    from plinth.testing import isolate, make_guards

    _guards = make_guards(forbidden={"make", "ssh"})   # + PLATFORM_PROGRAMS, always
    _isolated = isolate("hello")                        # HELLO_CONFIG, HELLO_DATA_DIR in tmp

| Fixture                   | Refuses                                                     |
|---------------------------|-------------------------------------------------------------|
| no outside process        | `subprocess.Popen` (hence run, call, check_output),         |
|                           | `os.system`, `os.posix_spawn[p]` of a forbidden program —   |
|                           | named directly, through a wrapper (`env`, `bash -c`, …) or  |
|                           | in a shell string                                           |
| no real signal            | `os.killpg` always; `os.kill` of a pid the test did not     |
|                           | start (a double carrying pid 1 once made `killpg(1, KILL)`  |
|                           | broadcast to every process of the user during a pytest)     |

An autouse guard is invisible: nothing tells "it protects" from "it stopped
protecting" until someone trips it. `assert_guards_armed` is the witness: it
trips each guard with an action that is HARMLESS if the guard is missing
(inert decoys carrying the forbidden names, signal 0), and fails if one does
not refuse. Call it from one test of the UI.

**`run_browser_check(app, script, …)`** starts the UI's daemon on a free port
in a thread and runs a Playwright script with Node:

    node <script> <playwright-dir> <base-url> <out-dir> <args…>

The script imports `helpers.mjs` from `process.env.PLINTH_HELPERS`. Without
`PLINTH_PLAYWRIGHT_DIR` (a folder whose `node_modules` holds playwright), it
FAILS rather than skips: a browser run that skips leaves a green line having
seen nothing.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from ..config import env_prefix
from ..server import App

# Every program plinth itself may launch (browser.py, terminal.py), and the
# desktop and service programs of the machines it runs on. Always forbidden.
PLATFORM_PROGRAMS = frozenset({
    "wt.exe", "wsl.exe", "cmd.exe", "explorer.exe", "powershell.exe", "pwsh.exe", "reg.exe",
    "wslview", "systemctl", "xdg-open", "open", "osascript",
    "x-terminal-emulator", "gnome-terminal", "konsole", "xterm",
})
# Programs that run another one given in their arguments.
WRAPPERS = frozenset({"env", "sh", "bash", "dash", "zsh", "sudo", "nohup", "setsid", "timeout",
                      "stdbuf", "xargs", "nice", "ionice", "exec", "command", "script"})
_SHELL_SPLIT = re.compile(r"[\s;&|()`<>]+")
PLAYWRIGHT_ENV = "PLINTH_PLAYWRIGHT_DIR"
BROWSER_OUT_ENV = "PLINTH_BROWSER_OUT"


def _names_in_shell_text(text: str) -> set[str]:
    return {Path(token.strip("'\"")).name for token in _SHELL_SPLIT.split(text) if token}


def program_names(argv: object, *, shell: bool = False) -> set[str]:
    """The names of the programs `argv` may start: argv[0], and the words of
    the rest when argv[0] is a wrapper or a shell runs the line."""
    if isinstance(argv, bytes):
        argv = argv.decode(errors="replace")
    if isinstance(argv, os.PathLike):
        argv = os.fspath(argv)
    if isinstance(argv, str):
        return _names_in_shell_text(argv) if shell else {Path(argv).name}
    if not isinstance(argv, (list, tuple)) or not argv:
        return set()
    words = [os.fspath(a) if isinstance(a, os.PathLike) else
             a.decode(errors="replace") if isinstance(a, bytes) else str(a) for a in argv]
    names = {Path(words[0]).name}
    if shell or Path(words[0]).name in WRAPPERS:
        for word in words[1:]:
            names |= _names_in_shell_text(word)
    return names


def make_guards(forbidden: Iterable[str]) -> Any:
    """ONE autouse fixture that forbids `forbidden` and PLATFORM_PROGRAMS, and
    every real signal. Assign it to a name in `conftest.py`: pytest registers
    a fixture found under a module-level name (a tuple of fixtures under one
    name would register nothing, silently; `assert_guards_armed` catches it)."""
    names = PLATFORM_PROGRAMS | frozenset(forbidden)

    def check(argv: object, shell: bool) -> None:
        hit = program_names(argv, shell=shell) & names
        if hit:
            raise AssertionError(
                f"this test launches a real {sorted(hit)[0]!r}: a forbidden program "
                f"(plinth.testing.make_guards). Point the code at a fake executable "
                f"carrying another name, or monkeypatch the call. argv: {argv!r}")

    @pytest.fixture(autouse=True, name="_plinth_guards")
    def guards(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
        # -- no outside process ---------------------------------------------------
        real_popen = subprocess.Popen
        spawned: set[int] = set()
        request.node.plinth_spawned_pids = spawned

        class GuardedPopen(real_popen):  # type: ignore[valid-type,misc]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                check(args[0] if args else kwargs.get("args"), bool(kwargs.get("shell")))
                super().__init__(*args, **kwargs)
                spawned.add(self.pid)

        real_system, real_spawn, real_spawnp = os.system, os.posix_spawn, os.posix_spawnp

        def guarded_system(command: Any) -> int:
            check(command, True)
            return real_system(command)

        def guarded_spawn(path: Any, argv: Any, *args: Any, **kwargs: Any) -> int:
            check([path, *list(argv)[1:]], False)
            return real_spawn(path, argv, *args, **kwargs)

        def guarded_spawnp(file: Any, argv: Any, *args: Any, **kwargs: Any) -> int:
            check([file, *list(argv)[1:]], False)
            return real_spawnp(file, argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
        monkeypatch.setattr(os, "system", guarded_system)
        monkeypatch.setattr(os, "posix_spawn", guarded_spawn)
        monkeypatch.setattr(os, "posix_spawnp", guarded_spawnp)

        # -- no real signal -------------------------------------------------------
        real_kill = os.kill

        def refuse_killpg(pgid: int, sig: int) -> None:
            raise AssertionError(
                f"os.killpg({pgid}, {sig}): a test tried to signal a real process group. "
                f"Replace os.killpg with a recorder in this test, and check the pid your "
                f"Popen double carries.")

        def guarded_kill(pid: int, sig: int) -> None:
            if not (isinstance(pid, int) and pid > 1 and pid in spawned):
                raise AssertionError(
                    f"os.kill({pid}, {sig}): a test tried to signal a process it did not "
                    f"start. Replace os.kill with a recorder in this test.")
            real_kill(pid, sig)

        monkeypatch.setattr(os, "killpg", refuse_killpg)
        monkeypatch.setattr(os, "kill", guarded_kill)

    return guards


def isolate(name: str) -> Any:
    """An autouse fixture: the UI `name` reads its config file and writes its
    data under the test's tmp_path, and no `<NAME>_*` variable of the user's
    environment leaks into a test."""
    prefix = env_prefix(name)

    @pytest.fixture(autouse=True, name=f"_plinth_isolated_{prefix.lower().rstrip('_')}")
    def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in list(os.environ):
            if key.startswith(prefix):
                monkeypatch.delenv(key)
        monkeypatch.setenv(prefix + "CONFIG", str(tmp_path / f"{name}-config.toml"))
        monkeypatch.setenv(prefix + "DATA_DIR", str(tmp_path / f"{name}-data"))

    return isolated


# -- the witness of the guards ------------------------------------------------------

def _decoy(folder: Path, name: str) -> str:
    """An inert executable carrying a forbidden NAME. If the guard were
    disarmed, this is what would run: nothing real, no window."""
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def _launch_attempts(decoy: str) -> list[tuple[str, Callable[[], object]]]:
    """Four ways to start `decoy`, each refused by an armed guard."""
    return [
        ("Popen", lambda: subprocess.Popen([decoy]).wait(10)),
        ("run via env", lambda: subprocess.run(["env", decoy], timeout=10)),
        ("shell line", lambda: subprocess.run(f"true && {decoy}", shell=True, timeout=10)),
        ("os.system", lambda: os.system(decoy)),
    ]


def assert_guards_armed(tmp_path: Path, programs: Iterable[str]) -> None:
    """Fail unless the guards of `make_guards` refuse each of `programs`
    (directly, through `env`, and in a shell line) and every real signal."""
    folder = tmp_path / "decoys"
    folder.mkdir(exist_ok=True)
    unarmed = []
    for name in sorted(set(programs)):
        for how, attempt in _launch_attempts(_decoy(folder, name)):
            try:
                attempt()
            except AssertionError as exc:
                if "launches a real" in str(exc):
                    continue
                raise
            unarmed.append(f"{name} ({how})")
    signals: list[tuple[str, Callable[[], object]]] = [
        ("os.killpg(1, 0)", lambda: os.killpg(1, 0)),
        ("os.kill(-1, 0)", lambda: os.kill(-1, 0))]
    for how, attempt in signals:
        try:
            attempt()
        except AssertionError:
            continue
        except OSError:
            pass
        unarmed.append(how)
    if unarmed:
        pytest.fail("guards not armed (install make_guards in conftest.py): "
                    + ", ".join(unarmed))
    started = subprocess.Popen(["sleep", "30"])
    started.terminate()
    if started.wait(timeout=10) == 0:
        pytest.fail("a process the test started could not be stopped by it")


# -- the browser harness -------------------------------------------------------------

def helpers_resource() -> Any:
    return resources.files("plinth") / "testing" / "helpers.mjs"


def run_browser_check(app: App, script: Path, *args: str, out_dir: Path | None = None,
                      timeout: float = 120.0) -> str:
    """Serve `app` on a free port and run the Playwright `script` against it;
    fail the test (never skip it) when the browser cannot run or the script
    fails. Return what the script printed. Screenshots go to `out_dir`, or to
    `PLINTH_BROWSER_OUT` when set (to look at them)."""
    pw_dir = os.environ.get(PLAYWRIGHT_ENV, "")
    node = shutil.which("node")
    if not pw_dir or not (Path(pw_dir) / "node_modules" / "playwright").is_dir():
        pytest.fail(f"set {PLAYWRIGHT_ENV} to a folder whose node_modules holds playwright "
                    f"(got {pw_dir!r}): a browser check never skips")
    if not node:
        pytest.fail("node is not on the PATH: a browser check never skips")
    chosen = os.environ.get(BROWSER_OUT_ENV) or out_dir or tempfile.mkdtemp(prefix="plinth-")
    out = Path(chosen)
    out.mkdir(parents=True, exist_ok=True)
    srv = app.make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, name="browser-check-http", daemon=True)
    thread.start()
    try:
        with resources.as_file(helpers_resource()) as helper:
            env = {**os.environ, "PLINTH_HELPERS": Path(helper).resolve().as_uri()}
            done = subprocess.run([node, str(script), pw_dir, srv.base_url(), str(out), *args],
                                  capture_output=True, text=True, timeout=timeout, env=env)
    finally:
        srv.shutdown()
        srv.server_close()
    if done.returncode != 0:
        pytest.fail(f"browser check {script.name} failed (exit {done.returncode}):\n"
                    f"{done.stdout}\n{done.stderr}")
    return done.stdout
