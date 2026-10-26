"""hello: the smallest UI built on plinth, and the one its browser tests drive.

    uv run python examples/hello/app.py     # then open http://127.0.0.1:8770

A list of named items, each with a tone; add one, change its tone through a
picker, open a terminal tab that greets it, delete it after a confirmation (a
locked one is refused, and the dialog says why). Everything else is plinth.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from plinth import App, Refused, Request, config, serve, terminal
from plinth.server import Response, file_response

HERE = Path(__file__).resolve().parent
TONES = ("ok", "warn", "error", "live", "human", "irreversible", "info")


@dataclass(frozen=True)
class Settings:
    port: int = field(default=8770, metadata={"min": 1, "max": 65535})


class Items:
    """The state, in memory, under a lock (the daemon serves in threads)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.serial = 0
        self.items: dict[str, dict[str, object]] = {}
        for name, tone, locked in (("Alpha", "ok", False), ("Bravo", "human", False),
                                   ("Charlie", "irreversible", True)):
            self.add(name, tone, locked)

    def add(self, name: str, tone: str = "info", locked: bool = False) -> dict[str, object]:
        with self.lock:
            self.serial += 1
            item = {"id": f"i{self.serial}", "name": name, "tone": tone, "locked": locked,
                    "created": time.time()}
            self.items[str(item["id"])] = item
            return dict(item)

    def get(self, item_id: str) -> dict[str, object]:
        with self.lock:
            return dict(self.items[item_id])          # KeyError: a 404 (map_error)

    def update(self, item_id: str, **changes: object) -> dict[str, object]:
        with self.lock:
            self.items[item_id].update(changes)
            return dict(self.items[item_id])

    def delete(self, item_id: str) -> None:
        with self.lock:
            del self.items[item_id]

    def all(self) -> list[dict[str, object]]:
        with self.lock:
            return [dict(item) for item in self.items.values()]


def build(port: int | None = None) -> App:
    settings = config.load("hello", Settings)
    app = App("hello", header="X-Hello", port=settings.port if port is None else port,
              code_dirs=[HERE])
    app.add_static("/static/hello", HERE / "static")
    app.map_error(KeyError, 404)
    items = Items()

    @app.route("GET", "/")
    def index(req: Request) -> Response:
        return file_response(HERE / "static" / "index.html")

    @app.route("GET", "/api/items")
    def listing(req: Request) -> object:
        return {"items": items.all(), "tones": TONES}

    @app.route("POST", "/api/items")
    def add(req: Request) -> object:
        name = str(req.json_object().get("name") or "").strip()
        if not name or len(name) > 60:
            raise Refused(400, "Type a name (60 characters at most).")
        return {"item": items.add(name), "message": f"Added {name}."}

    @app.route("POST", "/api/items/{item}/tone")
    def tone(req: Request) -> object:
        chosen = req.json_object().get("tone")
        if chosen not in TONES:
            raise Refused(400, f"Unknown tone {chosen!r}.")
        item = items.update(req.params["item"], tone=chosen)
        return {"item": item, "message": f"{item['name']} is now {chosen}."}

    @app.route("DELETE", "/api/items/{item}")
    def delete(req: Request) -> object:
        item = items.get(req.params["item"])
        if item["locked"]:
            raise Refused(409, f"{item['name']} is locked: it cannot be deleted.")
        items.delete(req.params["item"])
        return {"message": f"Deleted {item['name']}."}

    @app.route("POST", "/api/items/{item}/terminal")
    def greet(req: Request) -> object:
        item = items.get(req.params["item"])
        used = terminal.open_terminal([terminal.quote(["echo", f"Hello, {item['name']}!"])],
                                      f"hello-{item['id']}", data_dir=config.data_dir("hello"),
                                      keep_open=True)
        return {"message": f"A terminal tab greets {item['name']} ({used})."}

    return app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return serve(build(), on_ready=print)


if __name__ == "__main__":
    raise SystemExit(main())
