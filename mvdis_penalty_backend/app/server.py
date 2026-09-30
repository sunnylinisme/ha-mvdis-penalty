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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from mvdis import (
    CaptchaError,
    MvdisQuery,
    ParseError,
    QueryRejectedError,
    penalty_key,
)
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
MAX_PEOPLE = 10
MAX_SEEN_KEYS = 1000
OPTIONS_POLL_SECONDS = 5.0
OUTAGE_COOLDOWN = timedelta(minutes=30)
MANUAL_QUERY_GUARD = timedelta(minutes=5)
CAPTCHA_FAST_RETRY_DELAY = timedelta(seconds=5)
CAPTCHA_RETRY_DELAY = timedelta(minutes=15)
PROFILE_QUERY_DELAY_SECONDS = 2.0
OUTAGE_ERROR_TYPES = frozenset({"timeout", "network", "http"})
_NATIONAL_ID_CODES = {
    letter: value
    for letter, value in zip(
        "ABCDEFGHJKLMNPQRSTUVXYWZIO",
        range(10, 36),
        strict=True,
    )
}


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
    profile_name = clean_name or ("主要查詢人" if primary else "其他查詢人")
    if not clean_name or len(clean_name) > 30:
        raise ValueError(f"{profile_name}：名稱必須為 1 到 30 個字元")
    if not _valid_national_id(clean_uid):
        raise ValueError(f"{profile_name}：身分證字號格式或檢查碼不正確")
    if not re.fullmatch(r"\d{7}", clean_birthday):
        raise ValueError(f"{profile_name}：民國出生年月日必須為七碼數字")
    try:
        date(
            int(clean_birthday[:3]) + 1911,
            int(clean_birthday[3:5]),
            int(clean_birthday[5:]),
        )
    except ValueError as err:
        raise ValueError(f"{profile_name}：民國出生年月日不是有效日期") from err
    return Person(
        key="primary" if primary else _person_key(clean_uid, key_salt),
        name=clean_name,
        uid=clean_uid,
        birthday=clean_birthday,
        primary=primary,
    )


def _valid_national_id(value: str) -> bool:
    """Validate the format and checksum of a Taiwan national ID."""
    if not re.fullmatch(r"[A-Z][12]\d{8}", value):
        return False
    code = _NATIONAL_ID_CODES[value[0]]
    digits = [int(character) for character in value[1:]]
    checksum = code // 10 + (code % 10) * 9
    checksum += sum(
        digit * weight
        for digit, weight in zip(digits[:-1], range(8, 0, -1), strict=True)
    )
    checksum += digits[-1]
    return checksum % 10 == 0


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
        raise ValueError("其他查詢人設定必須是清單")
    if len(additional) + 1 > MAX_PEOPLE:
        raise ValueError(f"最多只能設定 {MAX_PEOPLE} 位查詢人")
    for value in additional:
        if not isinstance(value, dict):
            raise ValueError("每位其他查詢人都必須有完整設定")
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
        raise ValueError("同一身分證字號不能重複設定")
    return {
        "people": people,
        "scan_interval_hours": max(
            6, min(168, int(raw.get("scan_interval_hours", 24)))
        ),
        "max_retries": max(1, min(5, int(raw.get("max_retries", 1)))),
    }


def new_penalties(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    baseline: bool,
    seen_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return records that have never been reported for this profile."""
    if baseline:
        return []
    known_keys = {str(key) for key in (seen_keys or []) if isinstance(key, str) and key}
    previous_legacy_keys: set[str] = set()
    for item in previous:
        known_keys.add(str(item.get("key", "")))
        legacy_key = str(item.get("legacy_key", ""))
        if legacy_key:
            previous_legacy_keys.add(legacy_key)

    added: list[dict[str, Any]] = []
    for item in current:
        key = str(item.get("key", ""))
        legacy_key = str(item.get("legacy_key", ""))
        if key in known_keys:
            continue
        if (
            legacy_key
            and legacy_key in known_keys
            and legacy_key not in previous_legacy_keys
        ):
            continue
        added.append(item)
    return added


def merge_seen_keys(previous: list[str], current: list[dict[str, Any]]) -> list[str]:
    """Keep a bounded, insertion-ordered history of record identifiers."""
    keys = [key for key in previous if isinstance(key, str) and key]
    for item in current:
        for field in ("key", "legacy_key"):
            if item.get(field):
                keys.append(str(item[field]))
    return list(dict.fromkeys(keys))[-MAX_SEEN_KEYS:]


def total_amount(penalties: list[dict[str, Any]]) -> int:
    """Sum known penalty amounts."""
    total = 0
    for item in penalties:
        try:
            if item.get("amount") is not None:
                total += int(item["amount"])
        except (AttributeError, TypeError, ValueError):
            continue
    return total


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

    def restore_profile(self, person: Person, state: dict[str, Any]) -> None:
        """Restore all cached entities without producing events or notifications."""
        checked_at = state.get("checked_at")
        cached = {
            "checked_at": checked_at or "unknown",
            "penalties": (
                state.get("penalties", [])
                if isinstance(state.get("penalties"), list)
                else []
            ),
        }
        self.publish_result(
            person,
            cached,
            [],
            baseline=not bool(checked_at),
            cleared=False,
        )
        if state.get("error"):
            self.publish_error(
                person,
                str(state["error"]),
                str(state.get("error_type") or "unknown"),
                str(state.get("failed_at") or datetime.now(UTC).isoformat()),
            )

    def publish_test_notification(self, person: Person) -> bool:
        """Send a clearly marked notification without changing penalty state."""
        return self._post(
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

    def _post(self, path: str, payload: dict[str, Any]) -> bool:
        if not self._token:
            if not self._warned_missing_token:
                _LOGGER.warning(
                    "SUPERVISOR_TOKEN is unavailable; "
                    "Home Assistant updates are disabled"
                )
                self._warned_missing_token = True
            return False
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
            return True
        except requests.RequestException as err:
            _LOGGER.warning("Home Assistant API update failed for %s: %s", path, err)
            return False

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
        self._schedule_wake = threading.Event()
        self._state = self._load_state()
        self._last_refresh_at = self._state_datetime("last_attempt_at")
        self._last_full_refresh_at = (
            self._state_datetime("last_full_attempt_at") or self._last_refresh_at
        )
        self._next_refresh_at: datetime | None = None
        if not re.fullmatch(r"[0-9a-f]{64}", str(self._state.get("key_salt", ""))):
            self._state["key_salt"] = secrets.token_hex(32)
            self._save_state(self._state)

    def stop(self) -> None:
        self._stop.set()
        self._schedule_wake.set()

    def refresh(
        self,
        *,
        startup: bool = False,
        profile_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        with self._refresh_lock:
            return self._refresh(startup=startup, profile_keys=profile_keys)

    def _refresh(
        self,
        *,
        startup: bool = False,
        profile_keys: set[str] | None = None,
    ) -> dict[str, Any]:
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

        for person in options["people"]:
            self._publisher.restore_profile(
                person,
                people_state.get(person.key, _empty_person_state()),
            )
        selected_people = [
            person
            for person in options["people"]
            if profile_keys is None or person.key in profile_keys
        ]
        if not selected_people:
            return self.public_status()

        cooldown_until = self._cooldown_deadline()
        now = datetime.now(UTC)
        if cooldown_until and cooldown_until > now:
            _LOGGER.info(
                "MVDIS query skipped during connectivity cooldown until %s",
                cooldown_until.isoformat(),
            )
            return self.public_status()
        if cooldown_until:
            with self._state_lock:
                self._state.pop("cooldown_until", None)
                self._state.pop("cooldown_error_type", None)
                self._save_state(self._state)

        options_fingerprint = self._options_fingerprint()
        next_allowed = self._manual_query_deadline()
        stored_fingerprint = self._state.get("options_fingerprint")
        if (
            startup
            and next_allowed
            and next_allowed > now
            and options_fingerprint is not None
            and stored_fingerprint == options_fingerprint.hex()
        ):
            _LOGGER.info(
                "MVDIS startup query skipped; last attempt was recent (until %s)",
                next_allowed.isoformat(),
            )
            return self.public_status()

        attempted_at = datetime.now(UTC)
        with self._state_lock:
            self._state["last_attempt_at"] = attempted_at.isoformat()
            self._state["options_fingerprint"] = (
                options_fingerprint.hex() if options_fingerprint else None
            )
            self._last_refresh_at = attempted_at
            if profile_keys is None:
                self._state["last_full_attempt_at"] = attempted_at.isoformat()
                self._last_full_refresh_at = attempted_at
            self._save_state(self._state)

        for index, person in enumerate(selected_people, start=1):
            if index > 1 and self._stop.wait(PROFILE_QUERY_DELAY_SECONDS):
                break
            previous = people_state.get(person.key, _empty_person_state())
            stop_batch = False
            try:
                result = self._query.query(
                    person.uid,
                    person.birthday,
                    options["max_retries"],
                )
                penalties = [item.as_dict() for item in result.penalties]
                baseline = not bool(previous.get("checked_at"))
                added = new_penalties(
                    previous.get("penalties", []),
                    penalties,
                    baseline=baseline,
                    seen_keys=previous.get("seen_keys", []),
                )
                cleared = bool(
                    previous.get("checked_at")
                    and previous.get("penalties")
                    and not penalties
                )
                state = {
                    "checked_at": result.checked_at.isoformat(),
                    "penalties": penalties,
                    "seen_keys": merge_seen_keys(
                        previous.get("seen_keys", []), penalties
                    ),
                    "error": None,
                    "error_type": None,
                    "captcha_images": result.captcha_images,
                    "captcha_submissions": result.captcha_submissions,
                }
                with self._state_lock:
                    people_state[person.key] = state
                    self._save_state(self._state)
                self._publisher.publish_result(
                    person,
                    state,
                    added,
                    baseline=baseline,
                    cleared=cleared,
                )
                _LOGGER.info(
                    "MVDIS query succeeded for profile %s/%s: %s record(s), "
                    "%s new, %s CAPTCHA image(s), %s submission(s)",
                    index,
                    len(selected_people),
                    len(penalties),
                    len(added),
                    result.captcha_images,
                    result.captcha_submissions,
                )
            except Exception as err:  # One profile must not block the others.
                failed_at = datetime.now(UTC).isoformat()
                error_type = classify_error(err)
                _LOGGER.warning(
                    "MVDIS query failed for profile %s/%s: %s",
                    index,
                    len(selected_people),
                    err,
                )
                state = dict(previous)
                state["error"] = str(err)
                state["error_type"] = error_type
                state["failed_at"] = failed_at
                if error_type == "captcha":
                    retry_delay = (
                        CAPTCHA_FAST_RETRY_DELAY
                        if profile_keys is None
                        else CAPTCHA_RETRY_DELAY
                    )
                    state["captcha_retry_at"] = (
                        datetime.now(UTC) + retry_delay
                    ).isoformat()
                else:
                    state.pop("captcha_retry_at", None)
                with self._state_lock:
                    people_state[person.key] = state
                    self._save_state(self._state)
                self._publisher.publish_error(
                    person,
                    str(err),
                    error_type,
                    failed_at,
                )
                if error_type in OUTAGE_ERROR_TYPES:
                    stop_batch = True
                    cooldown_until = datetime.now(UTC) + OUTAGE_COOLDOWN
                    with self._state_lock:
                        self._state["cooldown_until"] = cooldown_until.isoformat()
                        self._state["cooldown_error_type"] = error_type
                    self._schedule_wake.set()
                    _LOGGER.warning(
                        "Stopping remaining profiles; connectivity cooldown until %s",
                        cooldown_until.isoformat(),
                    )
            with self._state_lock:
                self._save_state(self._state)
            if stop_batch:
                break
        with self._state_lock:
            self._last_refresh_at = datetime.now(UTC)
        return self.public_status()

    def trigger_refresh(self) -> bool:
        """Start a manual refresh if another refresh is not already running."""
        cooldown_until = self._cooldown_deadline()
        if cooldown_until and cooldown_until > datetime.now(UTC):
            return False
        next_allowed = self._manual_query_deadline()
        if next_allowed and next_allowed > datetime.now(UTC):
            return False
        if not self._refresh_lock.acquire(blocking=False):
            return False

        def run_reserved() -> None:
            try:
                self._refresh()
            finally:
                self._refresh_lock.release()
                self._schedule_wake.set()

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
        return self._publisher.publish_test_notification(person)

    def public_status(self) -> dict[str, Any]:
        """Return a snapshot safe to expose in the authenticated ingress UI."""
        configuration_error = None
        try:
            people = self._load_options()["people"]
        except Exception as err:
            people = []
            configuration_error = str(err)
        with self._state_lock:
            people_state = json.loads(json.dumps(self._state.get("people", {})))
            last_refresh_at = self._last_refresh_at
            next_refresh_at = self._next_refresh_at
            cooldown_until = self._cooldown_deadline()
            cooldown_error_type = self._state.get("cooldown_error_type")
            next_allowed_query_at = self._manual_query_deadline()
            captcha_retry_at = self._captcha_retry_deadline()
        if cooldown_until and cooldown_until <= datetime.now(UTC):
            cooldown_until = None
        if next_allowed_query_at and next_allowed_query_at <= datetime.now(UTC):
            next_allowed_query_at = None
        return {
            "version": 2,
            "refreshing": self._refresh_lock.locked(),
            "last_refresh_at": (
                last_refresh_at.isoformat() if last_refresh_at else None
            ),
            "next_refresh_at": (
                next_refresh_at.isoformat() if next_refresh_at else None
            ),
            "configuration_error": configuration_error,
            "cooldown_until": (cooldown_until.isoformat() if cooldown_until else None),
            "cooldown_error_type": cooldown_error_type if cooldown_until else None,
            "next_allowed_query_at": (
                next_allowed_query_at.isoformat() if next_allowed_query_at else None
            ),
            "captcha_retry_at": (
                captcha_retry_at.isoformat() if captcha_retry_at else None
            ),
            "people": [
                {
                    "key": person.key,
                    "name": person.name,
                    **_public_person_state(
                        people_state.get(person.key, _empty_person_state())
                    ),
                }
                for person in people
            ],
        }

    def run(self) -> None:
        self.refresh(startup=True)
        options_fingerprint = self._options_fingerprint()
        while not self._stop.is_set():
            try:
                interval = self._load_options()["scan_interval_hours"] * 3600
            except Exception as err:
                _LOGGER.error("Invalid add-on options: %s", err)
                interval = 3600
            with self._state_lock:
                now = datetime.now(UTC)
                anchor = self._last_full_refresh_at or now
                self._next_refresh_at = max(
                    now,
                    anchor + timedelta(seconds=interval),
                )
                deadline_reason = "regular"
                cooldown_until = self._cooldown_deadline()
                if cooldown_until and now < cooldown_until:
                    self._next_refresh_at = cooldown_until
                    deadline_reason = "cooldown"
                else:
                    captcha_retry_at = self._captcha_retry_deadline()
                    if captcha_retry_at and captcha_retry_at < self._next_refresh_at:
                        self._next_refresh_at = max(now, captcha_retry_at)
                        deadline_reason = "captcha"
                deadline = self._next_refresh_at
            options_changed = False
            wake_only = False
            while not self._stop.is_set():
                remaining = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
                if remaining == 0:
                    break
                if self._schedule_wake.wait(min(OPTIONS_POLL_SECONDS, remaining)):
                    self._schedule_wake.clear()
                    current_fingerprint = self._options_fingerprint()
                    if current_fingerprint != options_fingerprint:
                        options_fingerprint = current_fingerprint
                        options_changed = True
                        _LOGGER.info(
                            "Add-on options changed; validating and querying now"
                        )
                    else:
                        wake_only = True
                    break
                current_fingerprint = self._options_fingerprint()
                if current_fingerprint != options_fingerprint:
                    options_fingerprint = current_fingerprint
                    options_changed = True
                    _LOGGER.info("Add-on options changed; validating and querying now")
                    break
            with self._state_lock:
                self._next_refresh_at = None
            if self._stop.is_set():
                return
            if wake_only:
                continue
            if deadline_reason == "captcha" and not options_changed:
                due_keys = self._due_captcha_retry_keys(datetime.now(UTC))
                if due_keys:
                    _LOGGER.info(
                        "Retrying %s profile(s) after CAPTCHA failure",
                        len(due_keys),
                    )
                    self.refresh(profile_keys=due_keys)
                else:
                    continue
            else:
                self.refresh()
            if not options_changed:
                options_fingerprint = self._options_fingerprint()

    def _options_fingerprint(self) -> bytes | None:
        """Detect Supervisor option saves without exposing their sensitive values."""
        try:
            return hashlib.sha256(OPTIONS_PATH.read_bytes()).digest()
        except OSError:
            return None

    def _state_datetime(self, key: str) -> datetime | None:
        """Read one persisted timestamp as an aware UTC datetime."""
        value = self._state.get(key)
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _manual_query_deadline(self) -> datetime | None:
        """Return when another user-initiated query is safe to start."""
        attempted_at = self._state_datetime("last_attempt_at")
        return attempted_at + MANUAL_QUERY_GUARD if attempted_at else None

    def _captcha_retry_deadline(self) -> datetime | None:
        """Return the earliest pending per-profile CAPTCHA retry."""
        deadlines = [
            parsed
            for value in self._state.get("people", {}).values()
            if isinstance(value, dict)
            if (parsed := _parse_datetime(value.get("captcha_retry_at"))) is not None
        ]
        return min(deadlines, default=None)

    def _due_captcha_retry_keys(self, now: datetime) -> set[str]:
        """Return profiles whose delayed CAPTCHA retry is due."""
        return {
            str(key)
            for key, value in self._state.get("people", {}).items()
            if isinstance(value, dict)
            and (deadline := _parse_datetime(value.get("captcha_retry_at"))) is not None
            and deadline <= now
        }

    def _cooldown_deadline(self) -> datetime | None:
        """Return the persisted connectivity cooldown deadline, if valid."""
        with self._state_lock:
            value = self._state.get("cooldown_until")
        if not isinstance(value, str):
            return None
        try:
            deadline = datetime.fromisoformat(value)
        except ValueError:
            return None
        if deadline.tzinfo is None:
            return deadline.replace(tzinfo=UTC)
        return deadline.astimezone(UTC)

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
                    "version": 6,
                    "key_salt": value.get("key_salt"),
                    "cooldown_until": value.get("cooldown_until"),
                    "cooldown_error_type": value.get("cooldown_error_type"),
                    "last_attempt_at": value.get("last_attempt_at"),
                    "last_full_attempt_at": value.get("last_full_attempt_at"),
                    "options_fingerprint": value.get("options_fingerprint"),
                    "people": {
                        str(key): _normalize_person_state(person_state)
                        for key, person_state in value["people"].items()
                        if isinstance(person_state, dict)
                    },
                }
            if isinstance(value, dict) and "penalties" in value:
                return {
                    "version": 6,
                    "key_salt": None,
                    "people": {"primary": _normalize_person_state(value)},
                }
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as err:
            _LOGGER.warning(
                "State file is invalid; preserving it for recovery: %s", err
            )
            corrupt = STATE_PATH.with_name("state.corrupt.json")
            try:
                os.replace(STATE_PATH, corrupt)
                os.chmod(corrupt, 0o600)
            except OSError as move_err:
                _LOGGER.warning("Could not preserve invalid state file: %s", move_err)
        return {"version": 6, "key_salt": None, "people": {}}

    def _save_state(self, value: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = STATE_PATH.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, STATE_PATH)


def _empty_person_state() -> dict[str, Any]:
    return {
        "checked_at": None,
        "penalties": [],
        "seen_keys": [],
        "error": "Waiting for the first query",
        "error_type": "waiting",
    }


def _normalize_person_state(value: dict[str, Any]) -> dict[str, Any]:
    """Migrate one saved profile while retaining previously seen records."""
    state = dict(value)
    raw_penalties = state.get("penalties")
    penalties_by_key: dict[str, dict[str, Any]] = {}
    if isinstance(raw_penalties, list):
        for raw in raw_penalties:
            if not isinstance(raw, dict):
                continue
            raw_details = raw.get("details")
            details = (
                {str(key): str(item) for key, item in raw_details.items()}
                if isinstance(raw_details, dict)
                else {}
            )
            old_key = str(raw.get("key") or "")
            new_key = penalty_key(details) if details else old_key
            if not new_key:
                continue
            try:
                amount = int(raw["amount"]) if raw.get("amount") is not None else None
            except (TypeError, ValueError):
                amount = None
            item = {
                "key": new_key,
                "summary": str(raw.get("summary") or "交通違規罰單"),
                "amount": amount,
                "details": details,
            }
            legacy_key = str(raw.get("legacy_key") or old_key)
            if legacy_key and legacy_key != new_key:
                item["legacy_key"] = legacy_key
            penalties_by_key[new_key] = item
    penalties = list(penalties_by_key.values())
    state["penalties"] = penalties
    retry_at = _parse_datetime(state.get("captcha_retry_at"))
    if retry_at:
        state["captcha_retry_at"] = retry_at.isoformat()
    else:
        state.pop("captcha_retry_at", None)
    for field in ("captcha_images", "captcha_submissions"):
        try:
            value = int(state[field])
        except (KeyError, TypeError, ValueError):
            state.pop(field, None)
        else:
            if value >= 0:
                state[field] = value
            else:
                state.pop(field, None)
    seen = state.get("seen_keys")
    if not isinstance(seen, list):
        seen = []
    state["seen_keys"] = merge_seen_keys(seen, penalties)
    return state


def _public_person_state(value: dict[str, Any]) -> dict[str, Any]:
    """Expose only dashboard fields, excluding internal de-duplication history."""
    return {
        key: value.get(key)
        for key in (
            "checked_at",
            "penalties",
            "error",
            "error_type",
            "failed_at",
            "captcha_retry_at",
            "captcha_images",
            "captcha_submissions",
        )
        if key in value
    }


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an optional persisted timestamp as an aware UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
