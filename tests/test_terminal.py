"""A terminal tab on a script written to disk: any command, never a `;` on the
command line, a login shell, and the tab kept open when asked."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from plinth import terminal
from plinth.terminal import TerminalError, open_terminal, quote


@pytest.fixture
def fake_wt(tmp_path, monkeypatch):
    """A fake Windows Terminal on the PATH, under a name the guard allows; it
    records its arguments, one per line."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "wt-argv"
    fake = bin_dir / "fake-wt"
    fake.write_text(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {record}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(terminal, "WT", "fake-wt")
    monkeypatch.setattr(terminal, "CMD", "fake-cmd-absent")
    monkeypatch.setenv("PLINTH_PLATFORM", "wsl")
    monkeypatch.setenv("WSL_DISTRO_NAME", "TestDistro")
    return record


def test_on_wsl_a_tab_of_the_current_window_runs_the_script_in_a_login_shell(tmp_path, fake_wt):
    data = tmp_path / "data"
    used = open_terminal(["echo hi"], "hello-t1", data_dir=data, keep_open=True)
    assert used == "fake-wt"
    argv = fake_wt.read_text().splitlines()
    script = argv[-1]
    assert argv == ["-w", "0", "new-tab", "--title", "hello-t1", "--", "wsl.exe", "-d",
                    "TestDistro", "-e", "bash", "-l", script]
    assert Path(script).parent == data / "launch"
    assert not [a for a in argv if ";" in a]


def test_the_script_runs_any_command_exactly_and_deletes_itself(tmp_path, fake_wt):
    out = tmp_path / "out with space.txt"
    tricky = "it's; $(not run) `nor this` & done"
    lines = [quote([sys.executable, "-c",
                    "import sys; open(sys.argv[1], 'w').write(sys.argv[2])", str(out), tricky])]
    open_terminal(lines, "hello-run", data_dir=tmp_path / "data", keep_open=False,
                  cwd=tmp_path)
    script = Path(fake_wt.read_text().splitlines()[-1])
    assert script.is_file()
    done = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    assert out.read_text() == tricky             # one argument, unchanged
    assert not script.exists()                   # deleted when it started


def test_the_script_runs_in_the_folder_it_is_given(tmp_path, fake_wt):
    folder = tmp_path / "work"
    folder.mkdir()
    open_terminal(["pwd > where.txt"], "hello-cwd", data_dir=tmp_path / "data",
                  keep_open=False, cwd=folder)
    script = fake_wt.read_text().splitlines()[-1]
    subprocess.run(["bash", script], timeout=30, stdin=subprocess.DEVNULL, check=True)
    assert (folder / "where.txt").read_text().strip() == str(folder)


def test_keep_open_leaves_a_login_shell_and_says_the_exit_code(tmp_path):
    kept = terminal.script_text(["false"], title="t", cwd=None, keep_open=True)
    assert kept.splitlines()[-3:] == [
        "status=$?",
        "printf '\\n[%s] finished, exit code %s. This tab stays open: type exit to close "
        "it.\\n' t \"$status\"",
        "exec bash -l"]
    closed = terminal.script_text(["false"], title="t", cwd=None, keep_open=False)
    assert "exec bash -l" not in closed and closed.splitlines()[-1] == "false"
    with pytest.raises(TypeError):
        open_terminal(["x"], "t", data_dir=tmp_path)  # type: ignore[call-arg]


def test_without_wt_the_tab_opens_through_cmd(tmp_path, fake_wt, monkeypatch):
    bin_dir = tmp_path / "bin"
    (bin_dir / "fake-wt").rename(bin_dir / "fake-cmd")
    monkeypatch.setattr(terminal, "WT", "fake-wt-absent")
    monkeypatch.setattr(terminal, "CMD", "fake-cmd")
    assert open_terminal(["true"], "t2", data_dir=tmp_path / "d", keep_open=False) == "fake-cmd"
    argv = fake_wt.read_text().splitlines()
    assert argv[:9] == ["/c", "start", "", "wsl.exe", "-d", "TestDistro", "-e", "bash", "-l"]


@pytest.mark.parametrize("title", ["", "a b", "a;b", "-x", "é", "x" * 65, "a'b"])
def test_a_title_that_wt_could_split_is_refused(tmp_path, fake_wt, title):
    with pytest.raises(TerminalError, match="title"):
        open_terminal(["true"], title, data_dir=tmp_path, keep_open=False)
    assert not fake_wt.exists()


def test_a_data_folder_that_wt_could_split_is_refused(tmp_path, fake_wt):
    for bad in ("with space", "semi;colon", "quote'd"):
        with pytest.raises(TerminalError, match="data folder"):
            open_terminal(["true"], "t", data_dir=tmp_path / bad, keep_open=False)
    assert not fake_wt.exists()


def test_no_argument_with_a_semicolon_ever_reaches_the_terminal(tmp_path):
    with pytest.raises(TerminalError, match="separator"):
        terminal.command(tmp_path / "s.sh", "t", platform="wsl", distro="a;b")
    with pytest.raises(TerminalError, match="separator"):
        terminal.command(tmp_path / "s.sh", "t", platform="linux", distro=None,
                         template=("kitty", "sh -c 'a; b'", "{script}"))


def test_a_template_wins_and_must_name_the_script(tmp_path):
    script = tmp_path / "s.sh"
    assert terminal.command(script, "t", platform="wsl", distro="D",
                            template=("kitty", "bash", "-l", "{script}")) == [
        "kitty", "bash", "-l", str(script)]
    with pytest.raises(TerminalError, match="{script}"):
        terminal.command(script, "t", platform="linux", distro=None, template=("kitty",))


def test_the_native_terminals_of_linux_and_macos(tmp_path, monkeypatch):
    script = tmp_path / "s.sh"
    monkeypatch.setattr(shutil, "which", lambda name: {"konsole": "/bin/konsole"}.get(name))
    assert terminal.command(script, "t", platform="linux", distro=None) == [
        "/bin/konsole", "-e", "bash", "-l", str(script)]
    monkeypatch.setattr(shutil, "which", lambda name: {"osascript": "/bin/osascript"}.get(name))
    argv = terminal.command(script, "t", platform="macos", distro=None)
    assert argv[0] == "/bin/osascript" and f"bash -l {script}" in argv[2]
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(TerminalError, match="no terminal found"):
        terminal.command(script, "t", platform="linux", distro=None)
    with pytest.raises(TerminalError, match="no terminal is known"):
        terminal.command(script, "t", platform="other", distro=None)


def test_a_terminal_that_fails_is_reported_with_its_output(tmp_path, fake_wt):
    fake = tmp_path / "bin" / "fake-wt"
    fake.write_text("#!/bin/sh\necho 'cannot open' >&2\nexit 5\n")
    with pytest.raises(TerminalError, match="exited 5: cannot open"):
        open_terminal(["true"], "t", data_dir=tmp_path / "d", keep_open=False)


def test_lines_are_a_sequence_not_one_string(tmp_path, fake_wt):
    with pytest.raises(TerminalError, match="sequence"):
        open_terminal("echo hi", "t", data_dir=tmp_path, keep_open=False)


def test_a_real_wt_is_never_launched_by_the_suite(tmp_path, monkeypatch):
    """The guard of conftest.py: the real name is refused even when found."""
    fake = tmp_path / "wt.exe"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PLINTH_PLATFORM", "wsl")
    monkeypatch.setenv("WSL_DISTRO_NAME", "TestDistro")
    with pytest.raises(AssertionError, match="launches a real 'wt.exe'"):
        open_terminal(["true"], "t", data_dir=tmp_path / "d", keep_open=False)
