"""Configuration of a UI: environment > TOML file > default, per UI name.

A UI declares its settings as a frozen dataclass whose fields have defaults,
and loads it under its name:

    @dataclass(frozen=True)
    class Settings:
        port: int = field(default=8770, metadata={"min": 1, "max": 65535})
        greeting: str = "hello"
        terminal: tuple[str, ...] = ()

    settings = config.load("hello", Settings)

| Source, highest first | For the field `port` of the UI `hello`            |
|-----------------------|---------------------------------------------------|
| environment           | `HELLO_PORT`                                      |
| TOML file             | `port = 8771` in `~/.config/hello/config.toml`    |
|                       | (the file itself: `HELLO_CONFIG`)                 |
| default               | the dataclass default                             |

The data folder is `~/.local/share/<name>`, or `<NAME>_DATA_DIR`.

Field types: `str`, `int`, `float`, `bool`, `Path`, `tuple[str, ...]`. From
the environment, a bool reads 1/0, true/false, yes/no, on/off; a tuple reads a
JSON array of strings. `metadata={"min": …, "max": …}` bounds a number. A key
of the TOML file that no field declares is an error, not a silence: a typo
would otherwise leave the default in place without a word. A field without
default must come from the environment or the file.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import tomllib
import typing
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,40}")
RESERVED_FIELDS = frozenset({"config", "data_dir"})
TRUE = frozenset({"1", "true", "yes", "on"})
FALSE = frozenset({"0", "false", "no", "off"})


class ConfigError(Exception):
    """The configuration file or an environment override is invalid."""


def env_prefix(name: str) -> str:
    """`hello` -> `HELLO_`, `my-ui` -> `MY_UI_`."""
    if not NAME_RE.fullmatch(name):
        raise ConfigError(f"name {name!r}: lowercase letters, digits and '-', a letter first")
    return name.upper().replace("-", "_") + "_"


def config_path(name: str) -> Path:
    env = os.environ.get(env_prefix(name) + "CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".config" / name / "config.toml"


def data_dir(name: str) -> Path:
    env = os.environ.get(env_prefix(name) + "DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / name


def _bounded(key: str, number: float, meta: typing.Mapping[str, Any]) -> None:
    low, high = meta.get("min"), meta.get("max")
    if low is not None and number < low:
        raise ConfigError(f"{key} must be at least {low}, got {number}")
    if high is not None and number > high:
        raise ConfigError(f"{key} must be at most {high}, got {number}")


def _from_env(key: str, text: str, kind: Any) -> Any:
    try:
        if kind is str:
            return text
        if kind is bool:
            lowered = text.strip().lower()
            if lowered in TRUE:
                return True
            if lowered in FALSE:
                return False
            raise ValueError(text)
        if kind is int:
            return int(text.strip())
        if kind is float:
            return float(text.strip())
        if kind is Path:
            return Path(text).expanduser()
        if kind == tuple[str, ...]:
            items = json.loads(text)
            if not (isinstance(items, list) and all(isinstance(i, str) for i in items)):
                raise ValueError(text)
            return tuple(items)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{key}={text!r}: not a valid {_kind_name(kind)}") from exc
    raise ConfigError(f"{key}: unsupported field type {kind!r}")


def _from_toml(key: str, value: object, kind: Any) -> Any:
    ok: bool
    if kind is str or kind is Path:
        ok = isinstance(value, str)
    elif kind is bool:
        ok = isinstance(value, bool)
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind == tuple[str, ...]:
        ok = isinstance(value, list) and all(isinstance(i, str) for i in value)
    else:
        raise ConfigError(f"{key}: unsupported field type {kind!r}")
    if not ok:
        raise ConfigError(f"{key} must be a {_kind_name(kind)}, got {value!r}")
    if kind is Path:
        return Path(str(value)).expanduser()
    if kind is float:
        return float(value)  # type: ignore[arg-type]
    if kind == tuple[str, ...]:
        return tuple(value)  # type: ignore[arg-type]
    return value


def _kind_name(kind: Any) -> str:
    if kind == tuple[str, ...]:
        return "list of strings"
    return getattr(kind, "__name__", str(kind))


def load[T](name: str, schema: type[T]) -> T:
    """The settings of the UI `name`, as an instance of the dataclass `schema`."""
    prefix = env_prefix(name)
    if not dataclasses.is_dataclass(schema):
        raise TypeError(f"{schema!r} is not a dataclass")
    hints = typing.get_type_hints(schema)
    fields = dataclasses.fields(schema)
    clash = {f.name for f in fields} & RESERVED_FIELDS
    if clash:
        raise TypeError(f"field names reserved by plinth: {sorted(clash)}")
    path = config_path(name)
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
    unknown = sorted(set(raw) - {f.name for f in fields})
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s) {unknown}; known: "
                          f"{sorted(f.name for f in fields)}")
    values: dict[str, Any] = {}
    for item in fields:
        kind = hints[item.name]
        env_key = prefix + item.name.upper()
        if env_key in os.environ:
            value = _from_env(env_key, os.environ[env_key], kind)
            where = env_key
        elif item.name in raw:
            value = _from_toml(f"{path}: {item.name}", raw[item.name], kind)
            where = f"{path}: {item.name}"
        elif item.default is not dataclasses.MISSING:
            values[item.name] = item.default
            continue
        elif item.default_factory is not dataclasses.MISSING:
            values[item.name] = item.default_factory()
            continue
        else:
            raise ConfigError(f"{item.name} has no default: set {env_key} or "
                              f"`{item.name} = …` in {path}")
        if kind in (int, float):
            _bounded(where, value, item.metadata)
        values[item.name] = value
    return schema(**values)
