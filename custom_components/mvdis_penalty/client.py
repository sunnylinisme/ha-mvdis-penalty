"""Local add-on API client for Taiwan MVDIS Penalty."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import BACKEND_URL
from .exceptions import MvdisConnectionError, ResponseParseError
from .models import MvdisData, Penalty


class MvdisBackendClient:
    """Read query results from the loopback-only MVDIS add-on backend."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def async_health(self) -> bool:
        """Return whether the local backend is ready."""
        try:
            async with self._session.get(
                f"{BACKEND_URL}/health", timeout=ClientTimeout(total=5)
            ) as response:
                return response.status == 200
        except (ClientError, TimeoutError):
            return False

    async def async_result(self) -> MvdisData:
        """Fetch the most recent backend result."""
        payload = await self._request("GET", "/result", timeout=10)
        return _parse_payload(payload)

    async def async_refresh(self) -> MvdisData:
        """Ask the backend to query MVDIS immediately."""
        payload = await self._request("POST", "/refresh", timeout=150)
        return _parse_payload(payload)

    async def _request(self, method: str, path: str, *, timeout: int) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{BACKEND_URL}{path}",
                timeout=ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (ClientError, TimeoutError) as err:
            raise MvdisConnectionError(
                "The MVDIS backend add-on is unavailable"
            ) from err
        if not isinstance(payload, dict):
            raise ResponseParseError("The MVDIS backend returned invalid JSON")
        return payload


def _parse_payload(payload: dict[str, Any]) -> MvdisData:
    if error := payload.get("error"):
        raise MvdisConnectionError(str(error))
    raw_penalties = payload.get("penalties", [])
    if not isinstance(raw_penalties, list):
        raise ResponseParseError("The MVDIS backend returned invalid penalties")

    penalties: list[Penalty] = []
    for item in raw_penalties:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        penalties.append(
            Penalty(
                key=str(item["key"]),
                fields={},
                amount=int(item["amount"]) if item.get("amount") is not None else None,
                summary_text=str(item.get("summary", "交通違規罰單")),
            )
        )

    checked_at = None
    if raw_checked_at := payload.get("checked_at"):
        try:
            checked_at = datetime.fromisoformat(
                str(raw_checked_at).replace("Z", "+00:00")
            )
        except ValueError as err:
            raise ResponseParseError(
                "The backend returned an invalid timestamp"
            ) from err
    if checked_at is None:
        checked_at = datetime.now(UTC)
    return MvdisData(penalties=tuple(penalties), checked_at=checked_at)
