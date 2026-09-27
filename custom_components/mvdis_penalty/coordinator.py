"""Data coordinator for Taiwan MVDIS Penalty."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MvdisBackendClient
from .const import (
    CONF_NOTIFY,
    DEFAULT_NOTIFY,
    DOMAIN,
    EVENT_NEW_PENALTY,
    STORAGE_VERSION,
)
from .exceptions import MvdisError
from .models import MvdisData, Penalty

_LOGGER = logging.getLogger(__name__)


class MvdisCoordinator(DataUpdateCoordinator[MvdisData]):
    """Poll MVDIS and emit an event only for newly observed penalties."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._seen_keys: set[str] | None = None
        self._baseline_initialized = False
        session = async_get_clientsession(hass)
        self._client = MvdisBackendClient(session)

    async def _async_update_data(self) -> MvdisData:
        """Fetch the latest MVDIS result."""
        if self._seen_keys is None:
            saved = await self._store.async_load() or {}
            self._seen_keys = set(saved.get("seen_keys", []))
            self._baseline_initialized = bool(saved.get("baseline_initialized", False))

        try:
            data = await self._client.async_result()
        except MvdisError as err:
            raise UpdateFailed(str(err)) from err

        current_keys = {item.key for item in data.penalties}
        if not self._baseline_initialized:
            # The first successful query establishes a baseline and does not
            # announce historical penalties as new.
            new_items: list[Penalty] = []
            self._baseline_initialized = True
        else:
            new_items = [
                item for item in data.penalties if item.key not in self._seen_keys
            ]

        self._seen_keys.update(current_keys)
        if len(self._seen_keys) > 500:
            self._seen_keys = set(list(self._seen_keys)[-500:])
        await self._store.async_save(
            {
                "baseline_initialized": self._baseline_initialized,
                "seen_keys": sorted(self._seen_keys),
            }
        )

        if new_items:
            self._announce_new_items(new_items)
        return data

    async def async_manual_refresh(self) -> None:
        """Request a new government-site query, then update all entities."""
        try:
            await self._client.async_refresh()
        except MvdisError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()

    def _announce_new_items(self, items: list[Penalty]) -> None:
        """Fire a Home Assistant event and optionally create a notification."""
        summaries = [item.summary for item in items]
        self.hass.bus.async_fire(
            EVENT_NEW_PENALTY,
            {
                "count": len(items),
                "summaries": summaries,
            },
        )
        if self.entry.options.get(CONF_NOTIFY, DEFAULT_NOTIFY):
            message = "\n".join(f"- {summary}" for summary in summaries)
            persistent_notification.async_create(
                self.hass,
                message,
                title=f"監理服務發現 {len(items)} 筆新罰單",
                notification_id=f"{DOMAIN}_new_penalty",
            )
