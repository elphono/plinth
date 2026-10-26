"""Configuration: environment > TOML file > default, under the UI's name."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from plinth import config
from plinth.config import ConfigError


@dataclass(frozen=True)
class Settings:
    port: int = field(default=8770, metadata={"min": 1, "max": 65535})
    greeting: str = "hello"
    ratio: float = 0.5
    verbose: bool = False
    folder: Path = Path("/tmp/default")
    terminal: tuple[str, ...] = ()


@pytest.fixture
def toml(tmp_path, monkeypatch):
    path = tmp_path / "ui.toml"
    monkeypatch.setenv("MY_UI_CONFIG", str(path))
    for name in ("PORT", "GREETING", "RATIO", "VERBOSE", "FOLDER", "TERMINAL", "DATA_DIR"):
        monkeypatch.delenv("MY_UI_" + name, raising=False)

    def write(text: str) -> Path:
        path.write_text(text)
        return path
    return write


def test_defaults_when_nothing_is_set(toml):
    assert config.load("my-ui", Settings) == Settings()


def test_the_file_overrides_the_defaults_and_the_environment_the_file(toml, monkeypatch):
    toml('port = 9001\ngreeting = "hi"\nratio = 2\nverbose = true\nfolder = "~/x"\n'
         'terminal = ["kitty", "{script}"]\n')
    loaded = config.load("my-ui", Settings)
    assert loaded == Settings(9001, "hi", 2.0, True, Path.home() / "x", ("kitty", "{script}"))
    monkeypatch.setenv("MY_UI_PORT", "9002")
    monkeypatch.setenv("MY_UI_GREETING", "hey")
    monkeypatch.setenv("MY_UI_VERBOSE", "off")
    monkeypatch.setenv("MY_UI_TERMINAL", '["xterm", "-e", "{script}"]')
    loaded = config.load("my-ui", Settings)
    assert (loaded.port, loaded.greeting, loaded.verbose, loaded.terminal) == (
        9002, "hey", False, ("xterm", "-e", "{script}"))
    assert loaded.ratio == 2.0            # still from the file


def test_an_unknown_key_of_the_file_is_an_error_not_a_silence(toml):
    toml("prot = 9001\n")
    with pytest.raises(ConfigError, match="unknown setting.*prot"):
        config.load("my-ui", Settings)


@pytest.mark.parametrize("text,why", [
    ("port = 0\n", "at least 1"), ("port = 70000\n", "at most 65535"),
    ('port = "80"\n', "must be a int"), ("port = true\n", "must be a int"),
    ("verbose = 1\n", "must be a bool"), ('terminal = "x"\n', "list of strings"),
    ("port = \n", "cannot read"),
])
def test_a_bad_file_value_is_refused_with_its_reason(toml, text, why):
    toml(text)
    with pytest.raises(ConfigError, match=why):
        config.load("my-ui", Settings)


@pytest.mark.parametrize("name,value,why", [
    ("PORT", "eighty", "not a valid int"), ("PORT", "0", "at least 1"),
    ("VERBOSE", "maybe", "not a valid bool"), ("TERMINAL", "xterm", "not a valid list"),
    ("TERMINAL", "[1]", "not a valid list"), ("RATIO", "x", "not a valid float"),
])
def test_a_bad_environment_value_is_refused_with_its_name(toml, monkeypatch, name, value, why):
    monkeypatch.setenv("MY_UI_" + name, value)
    with pytest.raises(ConfigError, match=f"MY_UI_{name}.*{why}|{why}"):
        config.load("my-ui", Settings)


def test_a_field_without_default_must_be_given(toml, monkeypatch):
    @dataclass(frozen=True)
    class Needs:
        token_file: Path

    with pytest.raises(ConfigError, match="no default"):
        config.load("my-ui", Needs)
    monkeypatch.setenv("MY_UI_TOKEN_FILE", "/x/y")
    assert config.load("my-ui", Needs).token_file == Path("/x/y")


def test_reserved_field_names_and_non_dataclasses_are_refused(toml):
    @dataclass(frozen=True)
    class Clash:
        data_dir: str = ""

    with pytest.raises(TypeError, match="reserved"):
        config.load("my-ui", Clash)
    with pytest.raises(TypeError, match="not a dataclass"):
        config.load("my-ui", dict)


def test_paths_follow_the_name_and_their_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_UI_CONFIG", raising=False)
    monkeypatch.delenv("MY_UI_DATA_DIR", raising=False)
    assert config.config_path("my-ui") == Path.home() / ".config" / "my-ui" / "config.toml"
    assert config.data_dir("my-ui") == Path.home() / ".local" / "share" / "my-ui"
    monkeypatch.setenv("MY_UI_DATA_DIR", str(tmp_path))
    assert config.data_dir("my-ui") == tmp_path
    assert config.env_prefix("my-ui") == "MY_UI_"


@pytest.mark.parametrize("name", ["", "My", "1x", "a b", "a_b"])
def test_a_bad_name_is_refused(name):
    with pytest.raises(ConfigError):
        config.env_prefix(name)


def test_the_example_ui_is_isolated_by_the_suite(tmp_path):
    """conftest.py installs plinth.testing.isolate("hello")."""
    assert config.data_dir("hello").is_relative_to(tmp_path)
    assert config.config_path("hello").is_relative_to(tmp_path)
