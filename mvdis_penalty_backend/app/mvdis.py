"""Synchronous MVDIS query and local CAPTCHA OCR."""

from __future__ import annotations

import hashlib
import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import certifi
import requests
from bs4 import BeautifulSoup
from ocr import LocalOcr, OcrDecision
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.mvdis.gov.tw"
QUERY_URL = f"{BASE_URL}/m3-emv-vil/vil/penaltyQueryPay?method=pagination"
POST_URL = f"{BASE_URL}/m3-emv-vil/vil/penaltyQueryPay"
CAPTCHA_URL = f"{BASE_URL}/m3-emv-vil/captchaImg.jpg"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Home Assistant; Taiwan MVDIS Penalty Backend) "
        "AppleWebKit/537.36 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
}
PAGE_TIMEOUT = (8, 20)
CAPTCHA_TIMEOUT = (8, 20)
POST_TIMEOUT = (8, 30)
CAPTCHA_IMAGE_LIMIT = 10
CAPTCHA_REFRESH_DELAY_SECONDS = 1.0

CAPTCHA_ERRORS = (
    "驗證碼錯誤",
    "驗證碼不正確",
    "驗證碼輸入錯誤",
    "驗證碼不符",
)
IDENTITY_ERRORS = (
    "身分證字號錯誤",
    "身分證字號或居留證格式錯誤",
    "身分證或居留證格式錯誤",
    "出生年月日錯誤",
    "生日格式錯誤",
    "輸入資料有誤",
    "查詢條件有誤",
)
EMPTY_PATTERNS = (
    re.compile(r"查無.{0,12}(?:違規|罰鍰|資料)"),
    re.compile(r"目前.{0,12}(?:無|沒有).{0,12}(?:違規|罰鍰)"),
    re.compile(r"無.{0,8}(?:交通)?違規紀錄"),
)
RESULT_HEADER_PARTS = (
    "違規日",
    "違規日期",
    "違規事實",
    "違規地點",
    "告發單",
    "應到案日",
    "罰鍰金額",
    "應繳金額",
)
SUMMARY_FIELDS = (
    "違規日",
    "違規日期",
    "違規事實",
    "違規地點",
    "應繳金額",
    "罰鍰金額",
)
PENALTY_NUMBER_PARTS = ("單號", "裁決書")
PENALTY_FALLBACK_IDENTITY_PARTS = (
    "違規日",
    "車號",
    "牌照",
    "違規事實",
    "違規地點",
)
LEGACY_IDENTITY_PARTS = ("單號", "違規日", "車號", "牌照")


class MvdisError(Exception):
    """Base backend error."""


class CaptchaError(MvdisError):
    """CAPTCHA was rejected or could not be recognized."""


class QueryRejectedError(MvdisError):
    """The supplied identity was rejected."""


class ParseError(MvdisError):
    """The result page no longer matches known markup."""


def mvdis_ssl_context() -> ssl.SSLContext:
    """Build a verified TLS context compatible with the current TWCA chain."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class MvdisTlsAdapter(HTTPAdapter):
    """Use the MVDIS-only TLS context without disabling verification."""

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        pool_kwargs["ssl_context"] = mvdis_ssl_context()
        super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )


@dataclass(frozen=True, slots=True)
class Penalty:
    key: str
    summary: str
    amount: int | None
    details: dict[str, str]
    legacy_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = {
            "key": self.key,
            "summary": self.summary,
            "amount": self.amount,
            "details": dict(self.details),
        }
        if self.legacy_key and self.legacy_key != self.key:
            value["legacy_key"] = self.legacy_key
        return value


@dataclass(frozen=True, slots=True)
class QueryResult:
    penalties: tuple[Penalty, ...]
    checked_at: datetime
    captcha_images: int = 1
    captcha_submissions: int = 1


class CaptchaSolver:
    """Thread-safe lazy wrapper around the local OCR model."""

    def __init__(self) -> None:
        self._ocr: LocalOcr | None = None

    def solve(self, image: bytes) -> str:
        return self.analyze(image).code

    def analyze(self, image: bytes) -> OcrDecision:
        if self._ocr is None:
            _LOGGER.info("Loading local CAPTCHA model")
            self._ocr = LocalOcr()
        return self._ocr.analyze(image)


class MvdisQuery:
    """Query MVDIS after selecting a reliable CAPTCHA in one cookie session."""

    def __init__(self) -> None:
        self._solver = CaptchaSolver()

    def query(self, uid: str, birthday: str, max_retries: int) -> QueryResult:
        images = 0
        submissions = 0
        best_decision: OcrDecision | None = None
        with requests.Session() as session:
            session.mount(
                f"{BASE_URL}/",
                MvdisTlsAdapter(max_retries=Retry(total=0, raise_on_status=False)),
            )
            session.headers.update(HEADERS)
            page = session.get(QUERY_URL, timeout=PAGE_TIMEOUT)
            page.raise_for_status()
            if "captchaImg.jpg" not in page.text or "queryPerson" not in page.text:
                raise ParseError("MVDIS query form was not found")

            for candidate in range(1, CAPTCHA_IMAGE_LIMIT + 1):
                if candidate > 1:
                    time.sleep(CAPTCHA_REFRESH_DELAY_SECONDS)
                captcha = session.get(
                    CAPTCHA_URL,
                    params={"_": time.time_ns(), "candidate": candidate},
                    headers={"Referer": QUERY_URL},
                    timeout=CAPTCHA_TIMEOUT,
                )
                captcha.raise_for_status()
                images += 1
                decision = self._solver.analyze(captcha.content)
                if best_decision is None or (
                    decision.agreement,
                    decision.confidence,
                ) > (
                    best_decision.agreement,
                    best_decision.confidence,
                ):
                    best_decision = decision
                _LOGGER.debug(
                    "CAPTCHA candidate %s/%s: agreement=%s/%s "
                    "confidence=%.3f reliable=%s",
                    candidate,
                    CAPTCHA_IMAGE_LIMIT,
                    decision.agreement,
                    decision.sample_count,
                    decision.confidence,
                    decision.reliable,
                )
                if not decision.reliable:
                    continue

                submissions += 1
                stage = (
                    "natural" if re.fullmatch(r"[A-Z][12]\d{8}", uid) else "foreigner"
                )
                response = session.post(
                    POST_URL,
                    data={
                        "stage": stage,
                        "method": "queryPerson",
                        "uid": uid.upper(),
                        "birthday": birthday,
                        "validateStr": decision.code,
                    },
                    headers={"Referer": QUERY_URL},
                    timeout=POST_TIMEOUT,
                )
                response.raise_for_status()
                compact = _compact_text(response.text)
                if _has_captcha_error(response.text, compact):
                    if submissions >= max_retries:
                        break
                    continue
                if any(message in compact for message in IDENTITY_ERRORS):
                    raise QueryRejectedError("MVDIS rejected the configured identity")
                result = parse_response(response.text)
                return QueryResult(
                    result.penalties,
                    result.checked_at,
                    captcha_images=images,
                    captcha_submissions=submissions,
                )

        best_summary = ""
        if best_decision is not None:
            best_summary = (
                "; best OCR agreement "
                f"{best_decision.agreement}/{best_decision.sample_count}, "
                f"confidence {best_decision.confidence:.3f}"
            )
        raise CaptchaError(
            "CAPTCHA selection failed after "
            f"{images} image(s) and {submissions} submission(s){best_summary}"
        )


def parse_response(html: str) -> QueryResult:
    soup = BeautifulSoup(html, "html.parser")
    compact = re.sub(r"\s+", "", soup.get_text(" "))
    if any(pattern.search(compact) for pattern in EMPTY_PATTERNS):
        return QueryResult((), datetime.now(UTC))

    penalties: dict[str, Penalty] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_index, headers = _find_headers(rows)
        if header_index < 0:
            continue
        for row in rows[header_index + 1 :]:
            cells = [_clean(cell.get_text(" ")) for cell in row.find_all("td")]
            if not cells or len(cells) < max(2, len(headers) // 2):
                continue
            fields = {
                headers[index]: value
                for index, value in enumerate(cells[: len(headers)])
                if value
            }
            if not fields:
                continue
            key = penalty_key(fields)
            penalties[key] = Penalty(
                key=key,
                summary=_summary(fields),
                amount=_amount(fields),
                details=fields,
                legacy_key=legacy_penalty_key(fields),
            )

    if penalties:
        return QueryResult(tuple(penalties.values()), datetime.now(UTC))
    if _has_captcha_error(html, compact):
        raise CaptchaError("MVDIS rejected the CAPTCHA")
    if any(message in compact for message in IDENTITY_ERRORS):
        raise QueryRejectedError("MVDIS rejected the configured identity")
    raise ParseError("No recognized result table or empty-result message")


def _find_headers(rows: list[Any]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows[:3]):
        candidate = [_clean(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
        if any(part in "".join(candidate) for part in RESULT_HEADER_PARTS):
            counts: dict[str, int] = {}
            result: list[str] = []
            for cell_index, header in enumerate(candidate):
                name = header or f"欄位{cell_index + 1}"
                counts[name] = counts.get(name, 0) + 1
                result.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
            return index, result
    return -1, []


def penalty_key(fields: dict[str, str]) -> str:
    """Return a stable identity that keeps distinct same-day penalties apart."""
    official_numbers = {
        key: value
        for key, value in fields.items()
        if any(part in key for part in PENALTY_NUMBER_PARTS)
    }
    fallback = {
        key: value
        for key, value in fields.items()
        if any(part in key for part in PENALTY_FALLBACK_IDENTITY_PARTS)
    }
    source = official_numbers or fallback or fields
    normalized = "|".join(f"{key}:{value}" for key, value in sorted(source.items()))
    return hashlib.sha256(normalized.encode()).hexdigest()[:20]


def legacy_penalty_key(fields: dict[str, str]) -> str:
    """Return the pre-0.6 identity so stored records can migrate safely."""
    stable = {
        key: value
        for key, value in fields.items()
        if any(part in key for part in LEGACY_IDENTITY_PARTS)
    }
    source = stable or fields
    normalized = "|".join(f"{key}:{value}" for key, value in sorted(source.items()))
    return hashlib.sha256(normalized.encode()).hexdigest()[:20]


def _summary(fields: dict[str, str]) -> str:
    values = [fields[key] for key in SUMMARY_FIELDS if fields.get(key)]
    return "｜".join(values) if values else "交通違規罰單"


def _amount(fields: dict[str, str]) -> int | None:
    for key, value in fields.items():
        if "金額" in key or "罰鍰" in key or key in {"應繳", "應納"}:
            digits = re.sub(r"[^0-9]", "", value)
            if digits:
                return int(digits)
    return None


def _compact_text(html: str) -> str:
    return re.sub(r"\s+", "", BeautifulSoup(html, "html.parser").get_text(" "))


def _has_captcha_error(html: str, compact: str) -> bool:
    """Detect CAPTCHA errors, including messages injected by JavaScript."""
    return any(message in compact or message in html for message in CAPTCHA_ERRORS)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \u3000:\uff1a")
