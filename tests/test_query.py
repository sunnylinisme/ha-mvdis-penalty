"""End-to-end request sequence tests for one MVDIS query attempt."""

from __future__ import annotations

from typing import Any

import mvdis
import pytest
from mvdis import CAPTCHA_URL, POST_URL, QUERY_URL, CaptchaError, MvdisQuery
from ocr import OcrDecision


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, *result_html: str) -> None:
        self.result_html = list(result_html)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.entries = 0

    def __enter__(self) -> FakeSession:
        self.entries += 1
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def mount(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        if url == QUERY_URL:
            return FakeResponse(text='queryPerson <img src="captchaImg.jpg">')
        if url == CAPTCHA_URL:
            return FakeResponse(content=b"captcha-image")
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        assert url == POST_URL
        return FakeResponse(text=self.result_html.pop(0))


def _decision(
    code: str,
    *,
    reliable: bool = True,
    agreement: int = 4,
    confidence: float = 0.9,
) -> OcrDecision:
    return OcrDecision(code, confidence, agreement, 4, reliable)


def _query_with_session(
    monkeypatch,
    session: FakeSession,
    *decisions: OcrDecision,
) -> MvdisQuery:
    monkeypatch.setattr("mvdis.requests.Session", lambda: session)
    monkeypatch.setattr(mvdis, "CAPTCHA_REFRESH_DELAY_SECONDS", 0)
    query = MvdisQuery()
    pending = list(decisions)
    monkeypatch.setattr(query._solver, "analyze", lambda image: pending.pop(0))
    return query


def test_one_attempt_uses_one_page_captcha_and_post(monkeypatch) -> None:
    session = FakeSession("<html><body>查無交通違規資料</body></html>")
    query = _query_with_session(monkeypatch, session, _decision("AB12"))

    result = query.query("A123456789", "0780702", max_retries=1)

    assert result.penalties == ()
    assert [(method, url) for method, url, _ in session.calls] == [
        ("GET", QUERY_URL),
        ("GET", CAPTCHA_URL),
        ("POST", POST_URL),
    ]
    post_data = session.calls[-1][2]["data"]
    assert post_data["validateStr"] == "AB12"
    assert post_data["stage"] == "natural"
    assert session.entries == 1


def test_rejected_captcha_does_not_hide_an_extra_attempt(monkeypatch) -> None:
    session = FakeSession("<script>$('#x').text('驗證碼輸入錯誤')</script>")
    query = _query_with_session(monkeypatch, session, _decision("AB12"))

    with pytest.raises(CaptchaError, match=r"1 image\(s\) and 1 submission"):
        query.query("A123456789", "0780702", max_retries=1)

    assert sum(method == "POST" for method, _url, _kwargs in session.calls) == 1
    assert len(session.calls) == 3


def test_low_confidence_images_are_replaced_without_posting(monkeypatch) -> None:
    session = FakeSession()
    query = _query_with_session(
        monkeypatch,
        session,
        *[_decision("ABC", reliable=False, agreement=1) for _ in range(10)],
    )

    with pytest.raises(CaptchaError, match=r"10 image\(s\) and 0 submission"):
        query.query("A123456789", "0780702", max_retries=1)

    page_gets = sum(
        method == "GET" and url == QUERY_URL for method, url, _ in session.calls
    )
    captcha_gets = sum(
        method == "GET" and url == CAPTCHA_URL for method, url, _ in session.calls
    )
    assert page_gets == 1
    assert captcha_gets == 10
    assert sum(method == "POST" for method, _url, _kwargs in session.calls) == 0


def test_easy_image_is_selected_after_skipping_uncertain_images(monkeypatch) -> None:
    session = FakeSession("<html><body>查無交通違規資料</body></html>")
    query = _query_with_session(
        monkeypatch,
        session,
        _decision("AB12", reliable=False, agreement=2),
        _decision("XY34", reliable=False, confidence=0.3),
        _decision("CD56"),
    )

    result = query.query("A123456789", "0780702", max_retries=1)

    assert result.captcha_images == 3
    assert result.captcha_submissions == 1
    page_gets = sum(
        method == "GET" and url == QUERY_URL for method, url, _ in session.calls
    )
    captcha_gets = sum(
        method == "GET" and url == CAPTCHA_URL for method, url, _ in session.calls
    )
    assert page_gets == 1
    assert captcha_gets == 3
    assert sum(method == "POST" for method, _url, _kwargs in session.calls) == 1
    assert session.calls[-1][2]["data"]["validateStr"] == "CD56"
    assert session.entries == 1


def test_second_submission_requires_explicit_retry_setting(monkeypatch) -> None:
    session = FakeSession(
        "<script>$('#x').text('驗證碼輸入錯誤')</script>",
        "<html><body>查無交通違規資料</body></html>",
    )
    query = _query_with_session(
        monkeypatch,
        session,
        _decision("AB12"),
        _decision("CD34"),
    )

    result = query.query("A123456789", "0780702", max_retries=2)

    assert result.captcha_images == 2
    assert result.captcha_submissions == 2
    assert sum(method == "POST" for method, _url, _kwargs in session.calls) == 2
