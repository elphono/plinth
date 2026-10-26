"""Conventions a test can hold, so that review does not have to.

No deployment value in the code: no home directory, no address but the
loopback (and never the all-interfaces one), no Windows user path. Found at
the AST, docstrings excluded, so that documentation may still QUOTE what it
forbids. Every module exposes its own exception.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sorted([*(ROOT / "src" / "plinth").rglob("*.py"), *(ROOT / "examples").rglob("*.py")])
TEXT_ASSETS = sorted([*(ROOT / "src" / "plinth" / "static").glob("*"),
                      *(ROOT / "src" / "plinth" / "testing").glob("*.mjs"),
                      *(ROOT / "examples" / "hello" / "static").glob("*")])

FORBIDDEN = (
    (r"/home/[A-Za-z0-9._-]+", "a home directory: use Path.home() or the config"),
    (r"(?i)c:\\+users", "a Windows user path"),
    (r"(?<![\d.])(?!127\.0\.0\.1\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
     "an IP address other than the loopback"),
    (r"(?i)\bubuntu\b|\bdebian\b", "a distribution name: use WSL_DISTRO_NAME"),
)


def _code_strings(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            head = body[0]
            if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant) \
                    and isinstance(head.value.value, str):
                docs.add(id(head.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docs and isinstance(node.value, str):
            yield node.lineno, node.value


def test_the_scan_covers_the_code():
    names = {p.name for p in PYTHON}
    assert {"server.py", "terminal.py", "app.py"} <= names
    assert {"ihm.css", "ihm.js", "helpers.mjs", "hello.js"} <= {p.name for p in TEXT_ASSETS}


def test_no_deployment_value_is_written_in_the_code():
    faults = []
    for path in PYTHON:
        for line, value in _code_strings(path):
            for pattern, why in FORBIDDEN:
                if re.search(pattern, value):
                    faults.append(f"{path.name}:{line}: {why} -> {value[:60]!r}")
    for path in TEXT_ASSETS:
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, why in FORBIDDEN:
                if re.search(pattern, text):
                    faults.append(f"{path.name}:{number}: {why} -> {text.strip()[:60]!r}")
    assert not faults, "\n".join(faults)


def test_the_detector_sees_what_it_forbids(tmp_path):
    sample = tmp_path / "witness.py"
    sample.write_text('A = "/home/someone/x"\nB = "C:\\\\Users\\\\x"\nC = "10.0.0.2"\n'
                      'D = "0.0.0.0"\nE = "Ubuntu-24.04"\nF = "127.0.0.1"\n')
    found = [why for _, value in _code_strings(sample)
             for pattern, why in FORBIDDEN if re.search(pattern, value)]
    assert len(found) == 5


def test_every_module_exposes_its_exception():
    missing = []
    for path in (ROOT / "src" / "plinth").glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)
                   and any(isinstance(b, ast.Name) and (b.id.endswith("Error") or b.id == "Exception")
                           for b in n.bases)]
        if not classes:
            missing.append(path.name)
    assert not missing, f"modules without their own exception: {missing}"


def test_the_package_declares_no_runtime_dependency():
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
    assert project["requires-python"] == ">=3.12"
