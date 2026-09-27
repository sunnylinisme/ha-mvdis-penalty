"""Manual refresh button for Taiwan MVDIS Penalty."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MvdisConfigEntry
from .entity import MvdisEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvdisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the manual refresh button."""
    async_add_entities([MvdisRefreshButton(entry)])


class MvdisRefreshButton(MvdisEntity, ButtonEntity):
    """Request an immediate MVDIS query."""

    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, entry: MvdisConfigEntry) -> None:
        """Initialize the button."""
        super().__init__(entry, "refresh")

    async def async_press(self) -> None:
        """Refresh coordinator data."""
        await self.coordinator.async_manual_refresh()
