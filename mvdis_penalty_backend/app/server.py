"""Scheduled MVDIS query with direct Home Assistant notifications."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from mvdis import CaptchaError, MvdisQuery, ParseError, QueryRejectedError
from web import DashboardServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("mvdis-penalty")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_PATH = DATA_DIR / "options.json"
STATE_PATH = DATA_DIR / "state.json"
HA_API = "http://supervisor/core/api"
MAX_PEOPLE = 5


@dataclass(frozen=True, slots=True)
class Person:
    """One authorized MVDIS query profile."""

    key: str
    name: str
    uid: str
    birthday: str
    primary: bool = False

    @property
    def entity_stem(self) -> str:
        return "mvdis_penalty" if self.primary else f"mvdis_penalty_{self.key}"


def _person_key(uid: str, key_salt: str) -> str:
    """Return a stable, installation-specific identifier for HA entity IDs."""
    return hmac.new(
        bytes.fromhex(key_salt), uid.encode("ascii"), hashlib.sha256
    ).hexdigest()[:10]


def _validate_person(
    name: Any,
    uid: Any,
    birthday: Any,
    *,
    primary: bool,
    key_salt: str,
) -> Person:
    clean_name = str(name or "").strip()
    clean_uid = str(uid or "").strip().upper()
    clean_birthday = str(birthday or "").strip()
    if not clean_name or len(clean_name) > 30:
        raise ValueError("Profile name must contain 1 to 30 characters")
    if not re.fullmatch(r"[A-Z][12]\d{8}", clean_uid):
        raise ValueError("National ID format is invalid")
    if not re.fullmatch(r"\d{7}", clean_birthday):
        raise ValueError("ROC birth date must contain seven digits")
    return Person(
        key="primary" if primary else _person_key(clean_uid, key_salt),
        name=clean_name,
        uid=clean_uid,
        birthday=clean_birthday,
        primary=primary,
    )


def parse_options(raw: dict[str, Any], *, key_salt: str) -> dict[str, Any]:
    """Validate add-on options and expand the primary and additional profiles."""
    people = [
        _validate_person(
            raw.get("primary_name", "主要查詢人"),
            raw.get("uid"),
            raw.get("birthday"),
            primary=True,
            key_salt=key_salt,
        )
    ]
    additional = raw.get("additional_people", [])
    if additional is None:
        additional = []
    if not isinstance(additional, list):
        raise ValueError("Additional people must be a list")
    if len(additional) + 1 > MAX_PEOPLE:
        raise ValueError(f"At most {MAX_PEOPLE} people can be configured")
    for value in additional:
        if not isinstance(value, dict):
            raise ValueError("Each additional person must be an object")
        people.append(
            _validate_person(
                value.get("name"),
                value.get("uid"),
                value.get("birthday"),
                primary=False,
                key_salt=key_salt,
            )
        )
    if len({person.uid for person in people}) != len(people):
        raise ValueError("The same identity cannot be configured more than once")
    return {
        "people": people,
        "scan_interval_hours": max(
            6, min(168, int(raw.get("scan_interval_hours", 24)))
        ),
        "max_retries": max(1, min(5, int(raw.get("max_retries", 3)))),
    }


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
        person: Person,
        state: dict[str, Any],
        added: list[dict[str, Any]],
        *,
        baseline: bool,
        cleared: bool = False,
    ) -> None:
        penalties = state["penalties"]
        amount = total_amount(penalties)
        common = {
            "checked_at": state["checked_at"],
            "summaries": [item["summary"] for item in penalties[:10]],
            "penalties": penalties[:10],
            "profile": person.name,
            "profile_key": person.key,
        }
        self._set_state(
            f"sensor.{person.entity_stem}_unpaid_count",
            len(penalties),
            {
                **common,
                "friendly_name": f"{person.name}監理站未繳罰單數",
                "icon": "mdi:car-brake-alert",
                "unit_of_measurement": "張",
            },
        )
        self._set_state(
            f"sensor.{person.entity_stem}_total_amount",
            amount,
            {
                **common,
                "friendly_name": f"{person.name}監理站罰單金額",
                "icon": "mdi:cash-multiple",
                "unit_of_measurement": "TWD",
            },
        )
        self._set_state(
            f"binary_sensor.{person.entity_stem}_has_unpaid",
            "on" if penalties else "off",
            {
                **common,
                "friendly_name": f"{person.name}監理站有未繳罰單",
                "icon": "mdi:alert-circle",
            },
        )
        self._set_state(
            f"sensor.{person.entity_stem}_last_check",
            state["checked_at"],
            {
                "friendly_name": f"{person.name}監理站罰單最後查詢",
                "device_class": "timestamp",
                "icon": "mdi:clock-check-outline",
                "profile": person.name,
                "profile_key": person.key,
            },
        )
        self._set_state(
            f"sensor.{person.entity_stem}_status",
            "ok",
            {
                "friendly_name": f"{person.name}監理站罰單查詢狀態",
                "baseline_created": baseline,
                "new_count": len(added),
                "icon": "mdi:check-network-outline",
                "profile": person.name,
                "profile_key": person.key,
            },
        )
        self._publish_group(person)
        if added:
            event = {
                "count": len(added),
                "summaries": [item["summary"] for item in added],
                "total_amount": total_amount(added),
                "checked_at": state["checked_at"],
                "profile": person.name,
                "profile_key": person.key,
            }
            self._post("/events/mvdis_penalty_new_case", event)
            self._post(
                "/services/persistent_notification/create",
                {
                    "notification_id": (
                        "mvdis_penalty_new_case"
                        if person.primary
                        else f"mvdis_penalty_new_case_{person.key}"
                    ),
                    "title": f"監理服務發現新罰單（{person.name}）",
                    "message": _notification_message(added),
                },
            )

        if cleared:
            event = {
                "checked_at": state["checked_at"],
                "profile": person.name,
                "profile_key": person.key,
            }
            self._post("/events/mvdis_penalty_cleared", event)
            self._post(
                "/services/persistent_notification/create",
                {
                    "notification_id": (
                        "mvdis_penalty_cleared"
                        if person.primary
                        else f"mvdis_penalty_cleared_{person.key}"
                    ),
                    "title": f"監理站已無未繳罰單（{person.name}）",
                    "message": (
                        "先前的未繳紀錄已不在本次查詢結果中，"
                        "請回監理服務網確認繳納或案件狀態。"
                    ),
                },
            )

    def publish_error(
        self,
        person: Person,
        error: str,
        error_type: str,
        failed_at: str,
    ) -> None:
        self._set_state(
            f"sensor.{person.entity_stem}_status",
            "error",
            {
                "friendly_name": f"{person.name}監理站罰單查詢狀態",
                "error": error[:500],
                "error_type": error_type,
                "failed_at": failed_at,
                "icon": "mdi:alert-network-outline",
                "profile": person.name,
                "profile_key": person.key,
            },
        )
        self._publish_group(person)

    def publish_test_notification(self, person: Person) -> None:
        """Send a clearly marked notification without changing penalty state."""
        self._post(
            "/services/persistent_notification/create",
            {
                "notification_id": f"mvdis_penalty_test_{person.key}",
                "title": f"監理站罰單通知測試（{person.name}）",
                "message": "這是一則測試通知，不代表查到罰單。",
            },
        )

    def remove_profile(self, profile_key: str) -> None:
        """Remove entities and the group left by a deleted profile."""
        stem = (
            "mvdis_penalty"
            if profile_key == "primary"
            else f"mvdis_penalty_{profile_key}"
        )
        for entity_id in (
            f"sensor.{stem}_unpaid_count",
            f"sensor.{stem}_total_amount",
            f"binary_sensor.{stem}_has_unpaid",
            f"sensor.{stem}_last_check",
            f"sensor.{stem}_status",
        ):
            self._delete(f"/states/{entity_id}")
        self._post("/services/group/remove", {"object_id": stem})

    def _publish_group(self, person: Person) -> None:
        """Create one searchable Home Assistant group for every person."""
        stem = person.entity_stem
        self._post(
            "/services/group/set",
            {
                "object_id": stem,
                "name": f"{person.name}監理站罰單",
                "icon": "mdi:car-brake-alert",
                "entities": [
                    f"sensor.{stem}_unpaid_count",
                    f"sensor.{stem}_total_amount",
                    f"binary_sensor.{stem}_has_unpaid",
                    f"sensor.{stem}_last_check",
                    f"sensor.{stem}_status",
                ],
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

    def _delete(self, path: str) -> None:
        if not self._token:
            return
        try:
            response = requests.delete(
                f"{HA_API}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as err:
            _LOGGER.warning("Home Assistant API delete failed for %s: %s", path, err)


class Addon:
    """Own configuration, query state, scheduling, and persistence."""

    def __init__(self) -> None:
        self._query = MvdisQuery()
        self._publisher = HomeAssistantPublisher()
        self._stop = threading.Event()
        self._refresh_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._state = self._load_state()
        if not re.fullmatch(r"[0-9a-f]{64}", str(self._state.get("key_salt", ""))):
            self._state["key_salt"] = secrets.token_hex(32)
            self._save_state(self._state)

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> dict[str, Any]:
        with self._refresh_lock:
            return self._refresh()

    def _refresh(self) -> dict[str, Any]:
        try:
            options = self._load_options()
        except Exception as err:
            _LOGGER.error("Invalid add-on options: %s", err)
            return dict(self._state)

        people_state = self._state.setdefault("people", {})
        active_keys = {person.key for person in options["people"]}
        for removed_key in set(people_state) - active_keys:
            self._publisher.remove_profile(removed_key)
            with self._state_lock:
                people_state.pop(removed_key, None)
                self._save_state(self._state)

        for index, person in enumerate(options["people"], start=1):
            previous = people_state.get(person.key, _empty_person_state())
            try:
                result = self._query.query(
                    person.uid,
                    person.birthday,
                    options["max_retries"],
                )
                penalties = [item.as_dict() for item in result.penalties]
                baseline = not bool(previous.get("checked_at"))
                added = new_penalties(
                    previous.get("penalties", []), penalties, baseline=baseline
                )
                cleared = bool(
                    previous.get("checked_at")
                    and previous.get("penalties")
                    and not penalties
                )
                state = {
                    "checked_at": result.checked_at.isoformat(),
                    "penalties": penalties,
                    "error": None,
                    "error_type": None,
                }
                with self._state_lock:
                    people_state[person.key] = state
                self._publisher.publish_result(
                    person,
                    state,
                    added,
                    baseline=baseline,
                    cleared=cleared,
                )
                _LOGGER.info(
                    "MVDIS query succeeded for profile %s/%s: %s record(s), %s new",
                    index,
                    len(options["people"]),
                    len(penalties),
                    len(added),
                )
            except Exception as err:  # One profile must not block the others.
                failed_at = datetime.now(UTC).isoformat()
                error_type = classify_error(err)
                _LOGGER.warning(
                    "MVDIS query failed for profile %s/%s: %s",
                    index,
                    len(options["people"]),
                    err,
                )
                state = dict(previous)
                state["error"] = str(err)
                state["error_type"] = error_type
                state["failed_at"] = failed_at
                with self._state_lock:
                    people_state[person.key] = state
                self._publisher.publish_error(
                    person,
                    str(err),
                    error_type,
                    failed_at,
                )
            with self._state_lock:
                self._save_state(self._state)
        return self.public_status()

    def trigger_refresh(self) -> bool:
        """Start a manual refresh if another refresh is not already running."""
        if not self._refresh_lock.acquire(blocking=False):
            return False

        def run_reserved() -> None:
            try:
                self._refresh()
            finally:
                self._refresh_lock.release()

        threading.Thread(
            target=run_reserved,
            name="mvdis-manual-refresh",
            daemon=True,
        ).start()
        return True

    def send_test_notification(self, profile_key: str) -> bool:
        """Send a test notification for one configured profile."""
        try:
            people = self._load_options()["people"]
        except Exception:
            return False
        person = next((item for item in people if item.key == profile_key), None)
        if person is None:
            return False
        self._publisher.publish_test_notification(person)
        return True

    def public_status(self) -> dict[str, Any]:
        """Return a snapshot safe to expose in the authenticated ingress UI."""
        try:
            people = self._load_options()["people"]
        except Exception:
            people = []
        with self._state_lock:
            people_state = json.loads(json.dumps(self._state.get("people", {})))
        return {
            "version": 1,
            "refreshing": self._refresh_lock.locked(),
            "people": [
                {
                    "key": person.key,
                    "name": person.name,
                    **people_state.get(person.key, _empty_person_state()),
                }
                for person in people
            ],
        }

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
        if not isinstance(raw, dict):
            raise ValueError("Add-on options must be an object")
        return parse_options(raw, key_salt=self._state["key_salt"])

    def _load_state(self) -> dict[str, Any]:
        try:
            with STATE_PATH.open(encoding="utf-8") as file:
                value = json.load(file)
            if isinstance(value, dict) and isinstance(value.get("people"), dict):
                return {
                    "version": 3,
                    "key_salt": value.get("key_salt"),
                    "people": value["people"],
                }
            if isinstance(value, dict) and "penalties" in value:
                return {
                    "version": 3,
                    "key_salt": None,
                    "people": {"primary": value},
                }
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"version": 3, "key_salt": None, "people": {}}

    def _save_state(self, value: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = STATE_PATH.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, STATE_PATH)


def _empty_person_state() -> dict[str, Any]:
    return {
        "checked_at": None,
        "penalties": [],
        "error": "Waiting for the first query",
        "error_type": "waiting",
    }


def classify_error(error: Exception) -> str:
    """Return a stable, user-facing error category without exposing identifiers."""
    if isinstance(error, CaptchaError):
        return "captcha"
    if isinstance(error, QueryRejectedError):
        return "identity"
    if isinstance(error, ParseError):
        return "response_format"
    if isinstance(error, requests.Timeout):
        return "timeout"
    if isinstance(error, requests.ConnectionError):
        return "network"
    if isinstance(error, requests.HTTPError):
        return "http"
    return "unknown"


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
    dashboard = DashboardServer(addon, port=8099)
    signal.signal(signal.SIGTERM, lambda *_: addon.stop())
    signal.signal(signal.SIGINT, lambda *_: addon.stop())
    _LOGGER.info("Taiwan MVDIS Penalty add-on started")
    dashboard.start()
    try:
        addon.run()
    finally:
        dashboard.stop()
    _LOGGER.info("Taiwan MVDIS Penalty add-on stopped")


if __name__ == "__main__":
    main()
