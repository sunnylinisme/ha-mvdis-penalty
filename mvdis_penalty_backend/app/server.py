"""Loopback API and scheduler for the MVDIS backend add-on."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mvdis import MvdisQuery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("mvdis-backend")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_PATH = DATA_DIR / "options.json"
STATE_PATH = DATA_DIR / "state.json"
HOST = "127.0.0.1"
PORT = 8099


class Backend:
    """Own configuration, query state, scheduling, and persistence."""

    def __init__(self) -> None:
        self._query = MvdisQuery()
        self._query_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._state = self._load_state()

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "query_running": self._query_lock.locked()}

    def result(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._state)

    def refresh(self) -> dict[str, Any]:
        with self._query_lock:
            try:
                options = self._load_options()
                result = self._query.query(
                    options["uid"],
                    options["birthday"],
                    options["max_retries"],
                )
                state = {
                    "checked_at": result.checked_at.isoformat(),
                    "penalties": [item.as_dict() for item in result.penalties],
                    "error": None,
                }
                _LOGGER.info(
                    "MVDIS query succeeded: %s record(s)", len(result.penalties)
                )
            except Exception as err:  # Query errors must not terminate the add-on.
                _LOGGER.warning("MVDIS query failed: %s", err)
                state = self.result()
                state["error"] = str(err)
                state["failed_at"] = datetime.now(UTC).isoformat()
            with self._state_lock:
                self._state = state
                self._save_state(state)
                return dict(state)

    def scheduler(self) -> None:
        self.refresh()
        while not self._stop.is_set():
            try:
                interval = self._load_options()["scan_interval_hours"] * 3600
            except Exception as err:
                _LOGGER.error("Invalid add-on options: %s", err)
                interval = 3600
            if self._stop.wait(interval):
                return
            self.refresh()

    def _load_options(self) -> dict[str, Any]:
        with OPTIONS_PATH.open(encoding="utf-8") as file:
            raw = json.load(file)
        uid = str(raw.get("uid", "")).strip().upper()
        birthday = str(raw.get("birthday", "")).strip()
        if not re.fullmatch(r"[A-Z][12]\d{8}", uid):
            raise ValueError("National ID format is invalid")
        if not re.fullmatch(r"\d{7}", birthday):
            raise ValueError("ROC birth date must contain seven digits")
        return {
            "uid": uid,
            "birthday": birthday,
            "scan_interval_hours": max(
                6, min(168, int(raw.get("scan_interval_hours", 24)))
            ),
            "max_retries": max(1, min(5, int(raw.get("max_retries", 3)))),
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            with STATE_PATH.open(encoding="utf-8") as file:
                value = json.load(file)
            if isinstance(value, dict):
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {
            "checked_at": None,
            "penalties": [],
            "error": "Waiting for the first query",
        }

    def _save_state(self, value: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = STATE_PATH.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, STATE_PATH)


BACKEND = Backend()


class Handler(BaseHTTPRequestHandler):
    """Small JSON-only loopback API."""

    server_version = "MvdisPenaltyBackend/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(BACKEND.health())
        elif self.path == "/result":
            self._json(BACKEND.result())
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/refresh":
            self._json(BACKEND.refresh())
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        _LOGGER.debug(format, *args)

    def _json(self, value: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    scheduler = threading.Thread(
        target=BACKEND.scheduler, name="scheduler", daemon=True
    )
    scheduler.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    _LOGGER.info("MVDIS backend listening on %s:%s", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("MVDIS backend stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
