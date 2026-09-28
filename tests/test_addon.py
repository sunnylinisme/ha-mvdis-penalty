"""Tests for add-on state comparison and Home Assistant publishing."""

import json
import threading
import time
from typing import Any

import requests
import server
from ocr import ctc_decode
from server import (
    _NATIONAL_ID_CODES,
    Person,
    _notification_message,
    classify_error,
    merge_seen_keys,
    new_penalties,
    parse_options,
    total_amount,
)
from web import DASHBOARD_HTML, DashboardServer

KEY_SALT = "01" * 32


def _item(key: str, amount: int | None = None) -> dict:
    return {"key": key, "summary": f"record-{key}", "amount": amount}


def _national_id(letter: str) -> str:
    body = [1, 2, 3, 4, 5, 6, 7, 8]
    code = _NATIONAL_ID_CODES[letter]
    subtotal = code // 10 + (code % 10) * 9
    subtotal += sum(
        digit * weight for digit, weight in zip(body, range(8, 0, -1), strict=True)
    )
    return letter + "".join(map(str, body)) + str(-subtotal % 10)


def test_first_result_is_baseline() -> None:
    assert new_penalties([], [_item("a")], baseline=True) == []


def test_only_unseen_records_are_new() -> None:
    added = new_penalties(
        [_item("a")],
        [_item("a"), _item("b", 1200)],
        baseline=False,
    )
    assert added == [_item("b", 1200)]


def test_seen_history_prevents_a_reappearing_record_notification() -> None:
    assert (
        new_penalties(
            [],
            [_item("old")],
            baseline=False,
            seen_keys=["old"],
        )
        == []
    )
    assert merge_seen_keys(["old"], [_item("new")]) == ["old", "new"]


def test_amount_and_notification_message() -> None:
    records = [_item("a", 1200), _item("b"), _item("c", 900)]
    assert total_amount(records) == 2100
    message = _notification_message(records)
    assert "新增 3 筆" in message
    assert "NT$ 2,100" in message


def test_publish_result_creates_entities_event_and_notification(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, json, **kwargs):
        calls.append((url, json))
        return Response()

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(server.requests, "post", fake_post)
    publisher = server.HomeAssistantPublisher()
    person = Person("primary", "本人", "A123456789", "0780702", primary=True)
    record = _item("new", 1200)
    publisher.publish_result(
        person,
        {
            "checked_at": "2026-09-27T12:00:00+00:00",
            "penalties": [record],
            "error": None,
        },
        [record],
        baseline=False,
    )

    urls = [url for url, _ in calls]
    assert f"{server.HA_API}/states/sensor.mvdis_penalty_unpaid_count" in urls
    assert f"{server.HA_API}/services/group/set" in urls
    assert f"{server.HA_API}/events/mvdis_penalty_new_case" in urls
    assert f"{server.HA_API}/services/persistent_notification/create" in urls
    group = next(
        payload for url, payload in calls if url.endswith("/services/group/set")
    )
    assert group["object_id"] == "mvdis_penalty"
    assert group["name"] == "本人監理站罰單"
    assert group["entities"] == [
        "sensor.mvdis_penalty_unpaid_count",
        "sensor.mvdis_penalty_total_amount",
        "binary_sensor.mvdis_penalty_has_unpaid",
        "sensor.mvdis_penalty_last_check",
        "sensor.mvdis_penalty_status",
    ]
    count_state = next(
        payload
        for url, payload in calls
        if url.endswith("/states/sensor.mvdis_penalty_unpaid_count")
    )
    assert count_state["attributes"]["penalties"] == [record]


def test_additional_people_have_stable_separate_entities(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, json, **kwargs):
        calls.append((url, json))
        return Response()

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(server.requests, "post", fake_post)
    publisher = server.HomeAssistantPublisher()
    person = Person("abc123", "家人", "B123456789", "0800101")
    record = _item("new", 600)
    publisher.publish_result(
        person,
        {"checked_at": "2026-09-27T12:00:00+00:00", "penalties": [record]},
        [record],
        baseline=False,
    )

    urls = [url for url, _ in calls]
    assert f"{server.HA_API}/states/sensor.mvdis_penalty_abc123_unpaid_count" in urls
    group = next(
        payload for url, payload in calls if url.endswith("/services/group/set")
    )
    assert group["object_id"] == "mvdis_penalty_abc123"
    assert group["name"] == "家人監理站罰單"
    event = next(
        payload
        for url, payload in calls
        if url.endswith("/events/mvdis_penalty_new_case")
    )
    assert event["profile"] == "家人"
    notification = next(
        payload
        for url, payload in calls
        if url.endswith("/services/persistent_notification/create")
    )
    assert notification["notification_id"] == "mvdis_penalty_new_case_abc123"


def test_parse_options_keeps_existing_single_person_configuration() -> None:
    options = parse_options(
        {"uid": "a123456789", "birthday": "0780702", "max_retries": 3},
        key_salt=KEY_SALT,
    )
    assert len(options["people"]) == 1
    assert options["people"][0].primary is True
    assert options["people"][0].uid == "A123456789"


def test_parse_options_defaults_to_one_captcha_attempt() -> None:
    options = parse_options(
        {"uid": "a123456789", "birthday": "0780702"},
        key_salt=KEY_SALT,
    )

    assert options["max_retries"] == 1


def test_parse_options_supports_five_people_and_rejects_duplicates() -> None:
    people = [
        {
            "name": f"家人 {index}",
            "uid": _national_id(letter),
            "birthday": "0800101",
        }
        for index, letter in enumerate("BCDE", start=1)
    ]
    options = parse_options(
        {
            "uid": "A123456789",
            "birthday": "0780702",
            "additional_people": people,
        },
        key_salt=KEY_SALT,
    )
    assert len(options["people"]) == 5
    assert len({person.key for person in options["people"]}) == 5

    try:
        parse_options(
            {
                "uid": "A123456789",
                "birthday": "0780702",
                "additional_people": [
                    {"name": "重複", "uid": "A123456789", "birthday": "0780702"}
                ],
            },
            key_salt=KEY_SALT,
        )
    except ValueError as err:
        assert "不能重複設定" in str(err)
    else:
        raise AssertionError("duplicate identity was accepted")


def test_parse_options_rejects_bad_checksum_and_impossible_birth_date() -> None:
    for uid, birthday in (
        ("A123456788", "0780702"),
        ("A123456789", "0780230"),
    ):
        try:
            parse_options(
                {"uid": uid, "birthday": birthday},
                key_salt=KEY_SALT,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid identity data was accepted")


def test_ctc_decode_matches_blank_and_repeat_rules() -> None:
    assert ctc_decode([0, 1, 1, 0, 1, 2, 2, 0], ["", "A", "7"]) == "AA7"


def test_legacy_state_is_migrated_to_primary_profile(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-09-27T12:00:00+00:00",
                "penalties": [_item("old")],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", state_path)
    addon = server.Addon()
    assert addon._state["version"] == 5
    assert addon._state["people"]["primary"]["penalties"] == [_item("old")]
    assert addon._state["people"]["primary"]["seen_keys"] == ["old"]


def test_error_categories_are_stable() -> None:
    assert classify_error(server.CaptchaError("bad")) == "captcha"
    assert classify_error(server.QueryRejectedError("bad")) == "identity"
    assert classify_error(server.ParseError("bad")) == "response_format"
    assert classify_error(requests.Timeout("bad")) == "timeout"
    assert classify_error(requests.ConnectionError("bad")) == "network"
    assert classify_error(RuntimeError("bad")) == "unknown"


def test_remove_profile_deletes_entities_and_group(monkeypatch) -> None:
    posts: list[tuple[str, dict[str, Any]]] = []
    deletes: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(
        server.requests,
        "post",
        lambda url, *, json, **kwargs: posts.append((url, json)) or Response(),
    )
    monkeypatch.setattr(
        server.requests,
        "delete",
        lambda url, **kwargs: deletes.append(url) or Response(),
    )
    publisher = server.HomeAssistantPublisher()
    publisher.remove_profile("abc123")

    assert len(deletes) == 5
    assert all("mvdis_penalty_abc123" in url for url in deletes)
    assert posts[-1][0].endswith("/services/group/remove")
    assert posts[-1][1] == {"object_id": "mvdis_penalty_abc123"}


def test_cleared_result_creates_confirmation(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(
        server.requests,
        "post",
        lambda url, *, json, **kwargs: calls.append((url, json)) or Response(),
    )
    publisher = server.HomeAssistantPublisher()
    person = Person("primary", "本人", "A123456789", "0780702", primary=True)
    publisher.publish_result(
        person,
        {
            "checked_at": "2026-09-28T12:00:00+00:00",
            "penalties": [],
            "error": None,
        },
        [],
        baseline=False,
        cleared=True,
    )

    urls = [url for url, _ in calls]
    assert f"{server.HA_API}/events/mvdis_penalty_cleared" in urls
    notification = next(
        payload
        for url, payload in calls
        if url.endswith("/services/persistent_notification/create")
    )
    assert notification["notification_id"] == "mvdis_penalty_cleared"


def test_restore_profile_recreates_entities_without_notifications(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(
        server.requests,
        "post",
        lambda url, *, json, **kwargs: calls.append((url, json)) or Response(),
    )
    publisher = server.HomeAssistantPublisher()
    person = Person("primary", "本人", "A123456789", "0780702", primary=True)

    publisher.restore_profile(
        person,
        {
            "checked_at": "2026-09-28T12:00:00+00:00",
            "penalties": [_item("cached", 900)],
            "error": "network unavailable",
            "error_type": "network",
            "failed_at": "2026-09-28T12:30:00+00:00",
        },
    )

    urls = [url for url, _ in calls]
    assert f"{server.HA_API}/states/sensor.mvdis_penalty_unpaid_count" in urls
    assert f"{server.HA_API}/states/sensor.mvdis_penalty_total_amount" in urls
    assert f"{server.HA_API}/states/binary_sensor.mvdis_penalty_has_unpaid" in urls
    assert f"{server.HA_API}/states/sensor.mvdis_penalty_last_check" in urls
    status_calls = [
        payload
        for url, payload in calls
        if url.endswith("/states/sensor.mvdis_penalty_status")
    ]
    assert status_calls[-1]["state"] == "error"
    assert not any("/events/" in url for url in urls)
    assert not any("persistent_notification/create" in url for url in urls)


def test_public_status_does_not_expose_identity(monkeypatch, tmp_path) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "primary_name": "本人",
                "uid": "A123456789",
                "birthday": "0780702",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(server, "OPTIONS_PATH", options_path)
    addon = server.Addon()

    status = addon.public_status()
    serialized = json.dumps(status, ensure_ascii=False)
    assert status["people"][0]["name"] == "本人"
    assert "A123456789" not in serialized
    assert "0780702" not in serialized
    assert "seen_keys" not in serialized


def test_public_status_includes_refresh_schedule(monkeypatch, tmp_path) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "primary_name": "本人",
                "uid": "A123456789",
                "birthday": "0780702",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(server, "OPTIONS_PATH", options_path)
    addon = server.Addon()
    addon._last_refresh_at = server.datetime(2026, 9, 28, 10, 0, tzinfo=server.UTC)
    addon._next_refresh_at = server.datetime(2026, 9, 29, 10, 0, tzinfo=server.UTC)

    status = addon.public_status()

    assert status["last_refresh_at"] == "2026-09-28T10:00:00+00:00"
    assert status["next_refresh_at"] == "2026-09-29T10:00:00+00:00"
    assert "最近更新：" in DASHBOARD_HTML
    assert "下次更新：" in DASHBOARD_HTML
    assert "每頁會自動更新狀態" not in DASHBOARD_HTML
    assert "監理服務網目前無法連線" in DASHBOARD_HTML


def test_connectivity_failure_stops_batch_and_starts_cooldown(
    monkeypatch, tmp_path
) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "primary_name": "本人",
                "uid": _national_id("A"),
                "birthday": "0780702",
                "additional_people": [
                    {
                        "name": "家人",
                        "uid": _national_id("B"),
                        "birthday": "0800101",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(server, "OPTIONS_PATH", options_path)
    addon = server.Addon()
    attempts: list[str] = []

    def fail_query(uid, birthday, max_retries):
        attempts.append(uid)
        raise requests.ConnectionError("blocked")

    monkeypatch.setattr(addon._query, "query", fail_query)
    monkeypatch.setattr(addon._publisher, "publish_error", lambda *args: None)

    status = addon.refresh()

    assert len(attempts) == 1
    assert status["cooldown_until"] is not None
    assert status["cooldown_error_type"] == "network"
    assert addon.trigger_refresh() is False
    assert len(attempts) == 1

    restarted = server.Addon()
    assert restarted.public_status()["cooldown_until"] == status["cooldown_until"]
    assert restarted.trigger_refresh() is False
    restored: list[str] = []
    monkeypatch.setattr(
        restarted._publisher,
        "restore_profile",
        lambda person, state: restored.append(person.name),
    )
    monkeypatch.setattr(
        restarted._query,
        "query",
        lambda *args: (_ for _ in ()).throw(AssertionError("query during cooldown")),
    )

    restarted.refresh()

    assert restored == ["本人", "家人"]


def test_public_status_explains_invalid_configuration_safely(
    monkeypatch, tmp_path
) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "primary_name": "本人",
                "uid": "A123456788",
                "birthday": "0780702",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(server, "OPTIONS_PATH", options_path)
    addon = server.Addon()

    status = addon.public_status()
    serialized = json.dumps(status, ensure_ascii=False)
    assert status["people"] == []
    assert "檢查碼" in status["configuration_error"]
    assert "本人" in status["configuration_error"]
    assert "A123456788" not in serialized
    assert "0780702" not in serialized
    assert "約 5 秒內自動驗證並查詢" in DASHBOARD_HTML


def test_saved_options_are_automatically_validated_and_queried(
    monkeypatch, tmp_path
) -> None:
    options_path = tmp_path / "options.json"
    options = {
        "primary_name": "本人",
        "uid": "A123456789",
        "birthday": "0780702",
        "scan_interval_hours": 24,
    }
    options_path.write_text(json.dumps(options), encoding="utf-8")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(server, "OPTIONS_PATH", options_path)
    monkeypatch.setattr(server, "OPTIONS_POLL_SECONDS", 0.01)
    addon = server.Addon()
    refreshes: list[str] = []
    monkeypatch.setattr(
        addon,
        "refresh",
        lambda: refreshes.append(options_path.read_text(encoding="utf-8")) or {},
    )

    worker = threading.Thread(target=addon.run)
    worker.start()
    deadline = time.monotonic() + 1
    while len(refreshes) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    options["primary_name"] = "修正後"
    options_path.write_text(json.dumps(options), encoding="utf-8")
    deadline = time.monotonic() + 1
    while len(refreshes) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    addon.stop()
    worker.join(timeout=1)

    assert len(refreshes) >= 2
    assert json.loads(refreshes[-1])["primary_name"] == "修正後"


def test_test_notification_reports_home_assistant_failure(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(
        server.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.ConnectionError("unavailable")
        ),
    )
    publisher = server.HomeAssistantPublisher()
    person = Person("primary", "本人", "A123456789", "0780702", primary=True)

    assert publisher.publish_test_notification(person) is False


def test_ingress_dashboard_status_and_actions(monkeypatch) -> None:
    class FakeAddon:
        refreshed = False
        notified = ""

        def public_status(self):
            return {"version": 1, "refreshing": False, "people": []}

        def trigger_refresh(self):
            self.refreshed = True
            return True

        def send_test_notification(self, profile_key):
            self.notified = profile_key
            return profile_key == "primary"

    monkeypatch.setenv("MVDIS_ALLOW_LOCAL_WEB", "1")
    addon = FakeAddon()
    dashboard = DashboardServer(addon, port=0)
    dashboard.start()
    try:
        base = f"http://127.0.0.1:{dashboard.port}"
        page = requests.get(base, timeout=2)
        assert page.status_code == 200
        assert "監理站罰單通知" in page.text
        assert "api/status" in DASHBOARD_HTML

        status = requests.get(f"{base}/api/status", timeout=2)
        assert status.json()["people"] == []

        health = requests.get(f"{base}/health", timeout=2)
        assert health.json() == {"status": "ok"}

        refresh = requests.post(
            f"{base}/api/refresh",
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=2,
        )
        assert refresh.status_code == 202
        assert addon.refreshed is True

        notification = requests.post(
            f"{base}/api/test-notification",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"profile_key": "primary"},
            timeout=2,
        )
        assert notification.status_code == 202
        assert addon.notified == "primary"
    finally:
        dashboard.stop()
