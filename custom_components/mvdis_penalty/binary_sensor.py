"""Binary sensor for Taiwan MVDIS Penalty."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MvdisConfigEntry
from .entity import MvdisEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvdisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the unpaid-penalty binary sensor."""
    async_add_entities([MvdisHasUnpaidPenalty(entry)])


class MvdisHasUnpaidPenalty(MvdisEntity, BinarySensorEntity):
    """Whether the current result contains an unpaid penalty."""

    _attr_translation_key = "has_unpaid_penalty"
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, entry: MvdisConfigEntry) -> None:
        """Initialize the binary sensor."""
        super().__init__(entry, "has_unpaid_penalty")

    @property
    def is_on(self) -> bool:
        """Return whether an unpaid penalty exists."""
        return self.coordinator.data.count > 0
