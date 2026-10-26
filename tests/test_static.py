"""The stylesheet and the page script: what a reader cannot check by eye."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "plinth" / "static"
CSS = (STATIC / "ihm.css").read_text(encoding="utf-8")
JS = (STATIC / "ihm.js").read_text(encoding="utf-8")
SEMANTIC = ("ok", "warn", "error", "live", "human", "irreversible", "info")


def _block(css: str, opener: str) -> dict[str, str]:
    """The custom properties declared in the block that starts with `opener`."""
    start = css.index(opener) + len(opener)
    end = css.index("}", start)
    return dict(re.findall(r"(--[a-z-]+):\s*([^;]+);", css[start:end]))


LIGHT = _block(CSS, ":root {")
DARK_MEDIA = _block(CSS, ':root:not([data-theme="light"]) {')
DARK_FORCED = _block(CSS, ':root[data-theme="dark"] {\n')


def test_the_reader_of_blocks_sees_what_it_should():
    # The witness of _block: it finds tokens, and finds a changed value.
    assert LIGHT["--ok"] == "#15803d" and DARK_MEDIA["--ok"] == "#22c55e"
    altered = CSS.replace("--ok: #22c55e;", "--ok: #000000;", 1)
    assert _block(altered, ':root:not([data-theme="light"]) {')["--ok"] == "#000000"


def test_the_two_dark_blocks_are_identical():
    """Dark under prefers-color-scheme and dark forced by data-theme: CSS has
    no way to write it once, so this test keeps the copies equal."""
    assert DARK_MEDIA == DARK_FORCED
    assert len(DARK_MEDIA) >= 30


def test_every_semantic_meaning_has_its_three_shades_in_light_and_dark():
    for meaning in SEMANTIC:
        for token in (f"--{meaning}", f"--{meaning}-text", f"--{meaning}-soft"):
            assert token in LIGHT, token
            assert token in DARK_MEDIA, token
        assert f".is-{meaning} {{" in CSS, meaning


def test_dark_redefines_only_tokens_that_exist_in_light():
    assert set(DARK_MEDIA) <= set(LIGHT)


def test_the_forced_light_theme_beats_the_dark_preference():
    assert ':root:not([data-theme="light"])' in CSS
    assert ':root[data-theme="light"] { color-scheme: light; }' in CSS
    assert ':root[data-theme="dark"] { color-scheme: dark; }' in CSS


def test_the_body_has_an_explicit_background():
    assert re.search(r"\bbody \{[^}]*background: var\(--page\)", CSS)


def test_the_page_script_is_a_classic_script_defining_ihm_only():
    assert not re.search(r"^\s*(import|export)\b", JS, re.MULTILINE)
    assert JS.lstrip().startswith("/*") and "(function (global) {" in JS
    assert JS.rstrip().endswith("})(window);")
    assert JS.count("global.Ihm = ") == 1


def _exported() -> set[str]:
    block = JS[JS.index("global.Ihm = Object.freeze({"):]
    block = block[:block.index("});")]
    return set(re.findall(r"\b([a-zA-Z]+)\b", block.split("{", 1)[1]))


def test_every_function_of_ihm_is_documented_in_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    exported = _exported()
    assert {"configure", "call", "act", "say", "poll", "confirm", "picker", "foldable"} <= exported
    missing = sorted(name for name in exported if f"Ihm.{name}" not in readme)
    assert not missing, f"Ihm functions the README does not document: {missing}"


def test_the_page_assets_reach_nothing_outside_the_daemon():
    """A local UI works offline: no CDN, no font service, no remote URL."""
    files = [*STATIC.glob("*"), *(ROOT / "examples" / "hello" / "static").glob("*")]
    assert len(files) >= 5
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"https?://|//cdn|@import", text), path.name
