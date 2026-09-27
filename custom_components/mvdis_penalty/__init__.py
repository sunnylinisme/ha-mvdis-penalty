"""Taiwan MVDIS Penalty integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS

if TYPE_CHECKING:
    from .coordinator import MvdisCoordinator

type MvdisConfigEntry = ConfigEntry[MvdisCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MvdisConfigEntry) -> bool:
    """Set up Taiwan MVDIS Penalty from a config entry."""
    from .coordinator import MvdisCoordinator

    coordinator = MvdisCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MvdisConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
