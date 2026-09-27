"""Tests for add-on state comparison and Home Assistant publishing."""

from typing import Any

import server
from server import _notification_message, new_penalties, total_amount


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
    record = _item("new", 1200)
    publisher.publish_result(
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
    assert f"{server.HA_API}/events/mvdis_penalty_new_case" in urls
    assert f"{server.HA_API}/services/persistent_notification/create" in urls
