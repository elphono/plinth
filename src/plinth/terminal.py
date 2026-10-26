"""Open a terminal TAB of the user on a script written to disk.

A UI hands over the lines of a bash script, any command it wants: plinth
writes them to a file, then opens a tab that runs it. What needs a human
answer (a confirmation, a password) is then typed in that tab, by the user,
to the program itself: the page never builds it.

    open_terminal([quote(["make", "check"])], "hello-check",
                  data_dir=config.data_dir("hello"), keep_open=True)

    wt.exe -w 0 new-tab --title hello-check -- wsl.exe -d <distro> -e bash -l <script>

Three constraints, measured in docpin, shape the call:

- **a TAB of the most recent window (`-w 0 new-tab`), never a new window**:
  a Windows Terminal set to `launchMode: fullscreen` applies it to new
  windows only;
- **no `;` anywhere in the arguments**: it is wt's command separator, and a
  `bash -c 'a; b'` was cut in two (the first half ran, the rest never did).
  Everything therefore goes into a script FILE, the arguments are plain words,
  and `command()` refuses any argument holding a `;`;
- **`bash -l`**: `wsl.exe -e` starts no login shell, so `~/.local/bin` is not
  on the PATH without it.

Outside WSL (plinth.platform) the same script runs in the first native
terminal found:

| Platform | Terminal                                                        |
|----------|-----------------------------------------------------------------|
| WSL      | `wt.exe` as above, else `cmd.exe /c start`                      |
| Linux    | `x-terminal-emulator`, `gnome-terminal`, `konsole`, `xterm`     |
| macOS    | Terminal.app through `osascript` (not measured)                 |
| any      | a `template` such as `["kitty", "bash", "-l", "{script}"]` wins |

The script deletes itself when it starts. `keep_open` (required: a UI decides
it) leaves a login shell in the tab once the lines are done, with their exit
code printed, so that their output stays readable.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import platform as machine

# Looked up on the PATH by name; module constants so that tests can point them
# at fakes (the suite guard refuses any program named wt.exe or cmd.exe).
WT = "wt.exe"
CMD = "cmd.exe"

# Native terminals of a Linux desktop, first found wins, with the arguments
# that come before the command they run.
LINUX_TERMINALS = (("x-terminal-emulator", ("-e",)), ("gnome-terminal", ("--",)),
                   ("konsole", ("-e",)), ("xterm", ("-e",)))
TITLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
UNSAFE_IN_PATH = frozenset(" ;\"'")


class TerminalError(Exception):
    """No terminal could be opened."""


def quote(argv: Sequence[str]) -> str:
    """One shell line running `argv` exactly: never compose one by hand."""
    return shlex.join(argv)


def script_text(lines: Sequence[str], *, title: str, cwd: Path | None, keep_open: bool) -> str:
    head = ["#!/bin/bash",
            f"# Written by plinth (terminal.py) for the tab {title}: one run, then deleted.",
            'rm -f -- "$0"']
    if cwd is not None:
        head.append(f"cd -- {shlex.quote(str(cwd))} || exit 1")
    tail = []
    if keep_open:
        tail = ["status=$?",
                f"printf '\\n[%s] finished, exit code %s. This tab stays open: "
                f"type exit to close it.\\n' {shlex.quote(title)} \"$status\"",
                "exec bash -l"]
    return "\n".join([*head, *lines, *tail]) + "\n"


def write_script(lines: Sequence[str], *, title: str, data_dir: Path, cwd: Path | None,
                 keep_open: bool) -> Path:
    """Write the script under `data_dir/launch/`; return its path."""
    folder = data_dir / "launch"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    script = folder / f"{title}-{stamp}.sh"
    if UNSAFE_IN_PATH & set(str(script)):
        raise TerminalError(f"{script}: the data folder must not contain spaces, quotes or "
                            f"';' (wt.exe would split the command)")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        script.write_text(script_text(lines, title=title, cwd=cwd, keep_open=keep_open),
                          encoding="utf-8")
        script.chmod(0o700)
    except OSError as exc:
        raise TerminalError(f"cannot write the launch script: {exc}") from exc
    return script


def command(script: Path, title: str, *, platform: str, distro: str | None,
            template: Sequence[str] = ()) -> list[str]:
    """The argv that opens the tab: `template` if given, else the platform's
    terminal (table above). Never an argument holding a `;`."""
    argv = _command(script, title, platform=platform, distro=distro, template=template)
    unsafe = [part for part in argv if ";" in part]
    if unsafe:
        raise TerminalError(f"a terminal argument holds ';', which wt.exe treats as a "
                            f"command separator: {unsafe}")
    return argv


def _command(script: Path, title: str, *, platform: str, distro: str | None,
             template: Sequence[str]) -> list[str]:
    if template:
        if not any("{script}" in part for part in template):
            raise TerminalError("the terminal template must contain {script}, where the "
                                "launch script goes")
        return [part.replace("{script}", str(script)) for part in template]
    if platform == "linux":
        for name, before in LINUX_TERMINALS:
            found = shutil.which(name)
            if found:
                return [found, *before, "bash", "-l", str(script)]
        raise TerminalError("no terminal found on the PATH (x-terminal-emulator, "
                            "gnome-terminal, konsole, xterm): pass a template")
    if platform == "macos":
        osascript = shutil.which("osascript")
        if not osascript:
            raise TerminalError("osascript not found: no terminal can be opened")
        # The script path holds no space, quote or ';' (write_script refuses them).
        return [osascript, "-e", f'tell application "Terminal" to do script "bash -l {script}"',
                "-e", 'tell application "Terminal" to activate']
    if platform != "wsl" or not distro:
        raise TerminalError(f"no terminal is known for this platform ({platform}): pass a "
                            f"template")
    wsl = ["wsl.exe", "-d", distro, "-e", "bash", "-l", str(script)]
    wt = shutil.which(WT)
    if wt:
        return [wt, "-w", "0", "new-tab", "--title", title, "--", *wsl]
    cmd = shutil.which(CMD)
    if cmd:
        # The empty "" is the window title `start` expects before the program.
        return [cmd, "/c", "start", "", *wsl]
    raise TerminalError("neither wt.exe nor cmd.exe is on the PATH: no terminal can be opened "
                        "(a daemon under systemd needs their folder on its PATH)")


def open_terminal(script_lines: Sequence[str], title: str, *, data_dir: Path, keep_open: bool,
                  cwd: Path | None = None, template: Sequence[str] = ()) -> str:
    """Open a tab running `script_lines` (bash, one element per line; build a
    command line with `quote`). `title` names the tab: letters, digits, `.`,
    `_`, `-`. Return the program used (`wt.exe`, `cmd.exe`, a native terminal,
    or the template's)."""
    if isinstance(script_lines, str):
        raise TerminalError("script_lines is a sequence of lines, not one string")
    if not TITLE_RE.fullmatch(title):
        raise TerminalError(f"title {title!r}: letters, digits, '.', '_', '-' (64 at most)")
    if cwd is not None and not Path(cwd).is_dir():
        raise TerminalError(f"the folder {cwd} does not exist")
    kind = machine.platform()
    try:
        distro = machine.wsl_distro() if kind == "wsl" and not template else None
    except machine.PlatformError as exc:
        raise TerminalError(str(exc)) from exc
    script = write_script(script_lines, title=title, data_dir=data_dir, cwd=cwd,
                          keep_open=keep_open)
    argv = command(script, title, platform=kind, distro=distro, template=template)
    # cmd.exe refuses a UNC working directory: run Windows programs from their own.
    run_in = os.path.dirname(argv[0]) if distro else None
    try:
        done = subprocess.run(argv, cwd=run_in or None, capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TerminalError(f"cannot start {argv[0]}: {exc}") from exc
    if done.returncode != 0:
        raise TerminalError(f"{os.path.basename(argv[0])} exited {done.returncode}: "
                            f"{(done.stderr or done.stdout).strip()[:300]}")
    return os.path.basename(argv[0])
