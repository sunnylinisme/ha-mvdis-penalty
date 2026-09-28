"""Tests for add-on state comparison and Home Assistant publishing."""

import json
from typing import Any

import requests
import server
from ocr import ctc_decode
from server import (
    Person,
    _notification_message,
    classify_error,
    new_penalties,
    parse_options,
    total_amount,
)
from web import DASHBOARD_HTML, DashboardServer

KEY_SALT = "01" * 32


def _item(key: str, amount: int | None = None) -> dict:
    return {"key": key, "summary": f"record-{key}", "amount": amount}


def test_first_result_is_baseline() -> None:
    assert new_penalties([], [_item("a")], baseline=True) == []


def test_only_unseen_records_are_new() -> None:
    added = new_penalties(
        [_item("a")],
        [_item("a"), _item("b", 1200)],
        baseline=False,
    )
    assert added == [_item("b", 1200)]


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


def test_parse_options_supports_five_people_and_rejects_duplicates() -> None:
    people = [
        {"name": f"家人 {index}", "uid": f"{letter}123456789", "birthday": "0800101"}
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
        assert "same identity" in str(err)
    else:
        raise AssertionError("duplicate identity was accepted")


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
    assert addon._state["version"] == 3
    assert addon._state["people"]["primary"]["penalties"] == [_item("old")]


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
        lambda url, *, json, **kwargs: (posts.append((url, json)) or Response()),
    )
    monkeypatch.setattr(
        server.requests,
        "delete",
        lambda url, **kwargs: (deletes.append(url) or Response()),
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
        lambda url, *, json, **kwargs: (calls.append((url, json)) or Response()),
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
