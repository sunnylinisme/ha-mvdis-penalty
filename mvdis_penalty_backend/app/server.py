"""Scheduled MVDIS query with direct Home Assistant notifications."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from mvdis import MvdisQuery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("mvdis-penalty")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_PATH = DATA_DIR / "options.json"
STATE_PATH = DATA_DIR / "state.json"
HA_API = "http://supervisor/core/api"


def new_penalties(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    baseline: bool,
) -> list[dict[str, Any]]:
    """Return records that were not present in the previous successful query."""
    if baseline:
        return []
    previous_keys = {str(item.get("key", "")) for item in previous}
    return [item for item in current if str(item.get("key", "")) not in previous_keys]


def total_amount(penalties: list[dict[str, Any]]) -> int:
    """Sum known penalty amounts."""
    return sum(
        int(item["amount"]) for item in penalties if item.get("amount") is not None
    )  # noqa: E501


class HomeAssistantPublisher:
    """Publish entities, events, and notifications through the Supervisor proxy."""

    def __init__(self) -> None:
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._warned_missing_token = False

    def publish_result(
        self,
        state: dict[str, Any],
        added: list[dict[str, Any]],
        *,
        baseline: bool,
    ) -> None:
        penalties = state["penalties"]
        amount = total_amount(penalties)
        common = {
            "checked_at": state["checked_at"],
            "summaries": [item["summary"] for item in penalties[:10]],
        }
        self._set_state(
            "sensor.mvdis_penalty_unpaid_count",
            len(penalties),
            {
                **common,
                "friendly_name": "監理站未繳罰單數",
                "icon": "mdi:car-brake-alert",
                "unit_of_measurement": "張",
            },
        )
        self._set_state(
            "sensor.mvdis_penalty_total_amount",
            amount,
            {
                **common,
                "friendly_name": "監理站罰單金額",
                "icon": "mdi:cash-multiple",
                "unit_of_measurement": "TWD",
            },
        )
        self._set_state(
            "binary_sensor.mvdis_penalty_has_unpaid",
            "on" if penalties else "off",
            {
                **common,
                "friendly_name": "監理站有未繳罰單",
                "icon": "mdi:alert-circle",
            },
        )
        self._set_state(
            "sensor.mvdis_penalty_last_check",
            state["checked_at"],
            {
                "friendly_name": "監理站罰單最後查詢",
                "device_class": "timestamp",
                "icon": "mdi:clock-check-outline",
            },
        )
        self._set_state(
            "sensor.mvdis_penalty_status",
            "ok",
            {
                "friendly_name": "監理站罰單查詢狀態",
                "baseline_created": baseline,
                "new_count": len(added),
                "icon": "mdi:check-network-outline",
            },
        )
        if added:
            event = {
                "count": len(added),
                "summaries": [item["summary"] for item in added],
                "total_amount": total_amount(added),
                "checked_at": state["checked_at"],
            }
            self._post("/events/mvdis_penalty_new_case", event)
            self._post(
                "/services/persistent_notification/create",
                {
                    "notification_id": "mvdis_penalty_new_case",
                    "title": "監理服務發現新罰單",
                    "message": _notification_message(added),
                },
            )

    def publish_error(self, error: str, failed_at: str) -> None:
        self._set_state(
            "sensor.mvdis_penalty_status",
            "error",
            {
                "friendly_name": "監理站罰單查詢狀態",
                "error": error[:500],
                "failed_at": failed_at,
                "icon": "mdi:alert-network-outline",
            },
        )

    def _set_state(
        self, entity_id: str, state: str | int, attributes: dict[str, Any]
    ) -> None:
        self._post(
            f"/states/{entity_id}",
            {"state": str(state), "attributes": attributes},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> None:
        if not self._token:
            if not self._warned_missing_token:
                _LOGGER.warning(
                    "SUPERVISOR_TOKEN is unavailable; "
                    "Home Assistant updates are disabled"
                )
                self._warned_missing_token = True
            return
        try:
            response = requests.post(
                f"{HA_API}{path}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as err:
            _LOGGER.warning("Home Assistant API update failed for %s: %s", path, err)


class Addon:
    """Own configuration, query state, scheduling, and persistence."""

    def __init__(self) -> None:
        self._query = MvdisQuery()
        self._publisher = HomeAssistantPublisher()
        self._stop = threading.Event()
        self._state = self._load_state()

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> dict[str, Any]:
        try:
            options = self._load_options()
            result = self._query.query(
                options["uid"],
                options["birthday"],
                options["max_retries"],
            )
            penalties = [item.as_dict() for item in result.penalties]
            baseline = not bool(self._state.get("checked_at"))
            added = new_penalties(
                self._state.get("penalties", []), penalties, baseline=baseline
            )
            state = {
                "checked_at": result.checked_at.isoformat(),
                "penalties": penalties,
                "error": None,
            }
            self._state = state
            self._save_state(state)
            self._publisher.publish_result(state, added, baseline=baseline)
            _LOGGER.info(
                "MVDIS query succeeded: %s record(s), %s new",
                len(penalties),
                len(added),
            )
            return dict(state)
        except Exception as err:  # Query errors must not terminate the add-on.
            failed_at = datetime.now(UTC).isoformat()
            _LOGGER.warning("MVDIS query failed: %s", err)
            state = dict(self._state)
            state["error"] = str(err)
            state["failed_at"] = failed_at
            self._state = state
            self._save_state(state)
            self._publisher.publish_error(str(err), failed_at)
            return dict(state)

    def run(self) -> None:
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


def _notification_message(added: list[dict[str, Any]]) -> str:
    summaries = "\n".join(f"- {item['summary']}" for item in added[:5])
    if len(added) > 5:
        summaries += f"\n- 另有 {len(added) - 5} 筆"
    amount = total_amount(added)
    amount_line = f"\n已辨識金額合計：NT$ {amount:,}" if amount else ""
    return f"新增 {len(added)} 筆監理站罰單紀錄：\n{summaries}{amount_line}"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    addon = Addon()
    signal.signal(signal.SIGTERM, lambda *_: addon.stop())
    signal.signal(signal.SIGINT, lambda *_: addon.stop())
    _LOGGER.info("Taiwan MVDIS Penalty add-on started")
    addon.run()
    _LOGGER.info("Taiwan MVDIS Penalty add-on stopped")


if __name__ == "__main__":
    main()
