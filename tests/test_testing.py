"""plinth.testing: the guards are armed in this suite, their witness fails when
they are not, and the browser harness fails rather than skips."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from plinth.server import App
from plinth.testing import (PLATFORM_PROGRAMS, assert_guards_armed, program_names,
                            run_browser_check)

ROOT = Path(__file__).resolve().parent.parent


def test_the_guards_of_this_suite_are_armed(tmp_path):
    assert_guards_armed(tmp_path, PLATFORM_PROGRAMS)


def test_killpg_and_foreign_kills_are_refused_and_own_children_are_not(tmp_path):
    with pytest.raises(AssertionError, match="os.killpg"):
        os.killpg(1, 0)
    with pytest.raises(AssertionError, match="did not start"):
        os.kill(-1, 0)
    pid_max = int(Path("/proc/sys/kernel/pid_max").read_text())
    with pytest.raises(AssertionError, match="did not start"):
        os.kill(pid_max + 1, 0)
    child = subprocess.Popen(["sleep", "30"])
    child.terminate()
    assert child.wait(timeout=10) != 0


@pytest.mark.parametrize("argv,shell,seen,unseen", [
    (["/usr/bin/wt.exe", "-w", "0"], False, "wt.exe", None),
    (["env", "A=1", "/usr/bin/systemctl", "--user"], False, "systemctl", None),
    (["bash", "-lc", "cd /x && xdg-open http://x"], False, "xdg-open", None),
    ("echo hi; wslview u", True, "wslview", None),
    ("/opt/wt.exe", False, "wt.exe", None),
    (["git", "commit", "-m", "open the door"], False, "git", "open"),
    (["python3", "-c", "print('xdg-open')"], False, "python3", "xdg-open"),
])
def test_program_names_see_through_wrappers_and_shell_lines(argv, shell, seen, unseen):
    names = program_names(argv, shell=shell)
    assert seen in names
    if unseen:
        assert unseen not in names               # an argument of a program is not a program


def _inner_pytest(folder: Path, conftest: str | None, test: str) -> subprocess.CompletedProcess:
    folder.mkdir()
    if conftest is not None:
        (folder / "conftest.py").write_text(textwrap.dedent(conftest))
    (folder / "test_inner.py").write_text(textwrap.dedent(test))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(folder)], cwd=folder, capture_output=True, text=True,
                          timeout=120, env=env)


INNER_TEST = """
    from plinth.testing import PLATFORM_PROGRAMS, assert_guards_armed

    def test_armed(tmp_path):
        assert_guards_armed(tmp_path, {"make"} | PLATFORM_PROGRAMS)
"""


def test_the_witness_fails_in_a_suite_that_did_not_install_the_guards(tmp_path):
    """Without the guards, the witness's actions are harmless (inert decoys,
    signal 0) and it must FAIL: a witness that passes anyway proves nothing."""
    done = _inner_pytest(tmp_path / "unguarded", None, INNER_TEST)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "guards not armed" in done.stdout
    assert "make (Popen)" in done.stdout and "os.killpg(1, 0)" in done.stdout


def test_a_ui_conftest_installs_the_guards_with_one_line(tmp_path):
    done = _inner_pytest(tmp_path / "guarded", """
        from plinth.testing import make_guards
        _guards = make_guards(forbidden={"make"})
    """, INNER_TEST)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout


def test_the_ui_programs_are_forbidden_on_top_of_the_platform_ones(tmp_path):
    done = _inner_pytest(tmp_path / "partial", """
        from plinth.testing import make_guards
        _guards = make_guards(forbidden=())
    """, INNER_TEST)
    assert done.returncode == 1
    assert "make (Popen)" in done.stdout and "wt.exe" not in done.stdout.split("not armed")[1]


def test_the_browser_harness_fails_rather_than_skips_without_playwright(tmp_path, monkeypatch):
    monkeypatch.delenv("PLINTH_PLAYWRIGHT_DIR", raising=False)
    app = App("ui", "X-Ui", 0, code_dirs=())
    with pytest.raises(pytest.fail.Exception, match="never skips"):
        run_browser_check(app, tmp_path / "check.mjs", out_dir=tmp_path)
    monkeypatch.setenv("PLINTH_PLAYWRIGHT_DIR", str(tmp_path / "no-playwright-here"))
    with pytest.raises(pytest.fail.Exception, match="never skips"):
        run_browser_check(app, tmp_path / "check.mjs", out_dir=tmp_path)


def test_isolate_points_the_ui_at_the_test_folder(tmp_path):
    assert os.environ["HELLO_DATA_DIR"].startswith(str(tmp_path))
    assert os.environ["HELLO_CONFIG"].startswith(str(tmp_path))
