"""The wheel carries the static files, and an installed plinth finds them.

The wheel is built, installed in an EMPTY folder, and read from an interpreter
that sees nothing else (`-I -S`: no site-packages, no PYTHONPATH): if the read
came from this repository's `src/`, the paths would say so.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ("plinth/static/ihm.css", "plinth/static/ihm.js", "plinth/testing/helpers.mjs",
           "plinth/py.typed", "plinth/server.py", "plinth/terminal.py")

PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import importlib.resources as resources
    import plinth
    static = resources.files("plinth") / "static"
    print(json.dumps({
        "file": plinth.__file__,
        "version": plinth.__version__,
        "css": static.joinpath("ihm.css").read_text(encoding="utf-8"),
        "listed": sorted(p.name for p in static.iterdir()),
    }))
""")


def test_the_wheel_installed_in_an_empty_folder_serves_ihm_css(tmp_path):
    uv = shutil.which("uv")
    assert uv, "uv is needed to build the wheel"
    dist = tmp_path / "dist"
    built = subprocess.run([uv, "build", "--wheel", "--out-dir", str(dist), str(ROOT)],
                           capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = list(dist.glob("plinth-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    missing = [name for name in SHIPPED if name not in names]
    assert not missing, f"not in the wheel: {missing}"
    assert not [n for n in names if "__pycache__" in n or n.startswith(("tests/", "examples/"))]

    site = tmp_path / "site"
    site.mkdir()
    assert not any(site.iterdir())
    installed = subprocess.run([uv, "pip", "install", "--no-deps", "--target", str(site),
                                "--python", sys.executable, str(wheels[0])],
                               capture_output=True, text=True, timeout=300)
    assert installed.returncode == 0, installed.stdout + installed.stderr

    probe = subprocess.run([sys.executable, "-I", "-S", "-c", PROBE, str(site)],
                           capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert probe.returncode == 0, probe.stderr
    seen = json.loads(probe.stdout)
    assert Path(seen["file"]).is_relative_to(site), seen["file"]
    assert seen["css"] == (ROOT / "src" / "plinth" / "static" / "ihm.css").read_text(
        encoding="utf-8")
    assert seen["listed"] == ["ihm.css", "ihm.js"]
    import plinth
    assert seen["version"] == plinth.__version__
