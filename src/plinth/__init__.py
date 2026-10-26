"""plinth: the shared base of local web UIs.

A library, not a framework: each UI keeps its own daemon, routes and page, and
assembles the pieces it needs.

| Module            | What it gives                                                |
|-------------------|--------------------------------------------------------------|
| `plinth.server`   | `App`, `route`, `Refused`, `serve`: the guarded daemon       |
| `plinth.client`   | `call`, `ensure_daemon`: talking to a running daemon         |
| `plinth.config`   | environment > TOML > default, per UI name                    |
| `plinth.platform` | which kind of machine this is (wsl, linux, macos)            |
| `plinth.browser`  | `open_url`                                                   |
| `plinth.terminal` | `open_terminal`: a terminal tab on a script written on disk  |
| `plinth.testing`  | pytest guards, their witnesses, `run_browser_check`          |
| `static/ihm.css`  | semantic tokens and components, light, dark and forced theme |
| `static/ihm.js`   | the `Ihm` namespace of the page                              |
"""
from __future__ import annotations

__version__ = "0.1.0"

from .server import App, Refused, Request, Response, ServerError, serve  # noqa: E402

__all__ = ["App", "Refused", "Request", "Response", "ServerError", "__version__", "serve"]
