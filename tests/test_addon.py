"""Tests for add-on state comparison and Home Assistant publishing."""

import json
from typing import Any

import server
from ocr import ctc_decode
from server import (
    Person,
    _notification_message,
    new_penalties,
    parse_options,
    total_amount,
)

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
    assert addon._state["version"] == 2
    assert addon._state["people"]["primary"]["penalties"] == [_item("old")]
