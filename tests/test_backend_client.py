"""Tests for local backend payload normalization."""

from custom_components.mvdis_penalty.client import _parse_payload


def test_parse_backend_payload() -> None:
    data = _parse_payload(
        {
            "checked_at": "2026-09-27T12:00:00+00:00",
            "error": None,
            "penalties": [
                {
                    "key": "abc123",
                    "summary": "115/01/02｜測試違規",
                    "amount": 1200,
                }
            ],
        }
    )
    assert data.count == 1
    assert data.total_amount == 1200
    assert data.penalties[0].summary == "115/01/02｜測試違規"
