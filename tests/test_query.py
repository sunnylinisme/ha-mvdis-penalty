"""End-to-end request sequence tests for one MVDIS query attempt."""

from __future__ import annotations

from typing import Any

import pytest
from mvdis import CAPTCHA_URL, POST_URL, QUERY_URL, CaptchaError, MvdisQuery


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, result_html: str) -> None:
        self.result_html = result_html
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __enter__(self) -> FakeSession:
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
        return FakeResponse(text=self.result_html)


def _query_with_session(monkeypatch, session: FakeSession, code: str) -> MvdisQuery:
    monkeypatch.setattr("mvdis.requests.Session", lambda: session)
    query = MvdisQuery()
    monkeypatch.setattr(query._solver, "solve", lambda image: code)
    return query


def test_one_attempt_uses_one_page_captcha_and_post(monkeypatch) -> None:
    session = FakeSession("<html><body>查無交通違規資料</body></html>")
    query = _query_with_session(monkeypatch, session, "AB12")

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


def test_rejected_captcha_does_not_hide_an_extra_attempt(monkeypatch) -> None:
    session = FakeSession("<script>$('#x').text('驗證碼輸入錯誤')</script>")
    query = _query_with_session(monkeypatch, session, "AB12")

    with pytest.raises(CaptchaError, match="after 1 attempts"):
        query.query("A123456789", "0780702", max_retries=1)

    assert sum(method == "POST" for method, _url, _kwargs in session.calls) == 1
    assert len(session.calls) == 3


def test_invalid_local_ocr_length_is_not_posted(monkeypatch) -> None:
    session = FakeSession("<html><body>unused</body></html>")
    query = _query_with_session(monkeypatch, session, "ABC")

    with pytest.raises(CaptchaError, match="after 1 attempts"):
        query.query("A123456789", "0780702", max_retries=1)

    assert [(method, url) for method, url, _ in session.calls] == [
        ("GET", QUERY_URL),
        ("GET", CAPTCHA_URL),
    ]
