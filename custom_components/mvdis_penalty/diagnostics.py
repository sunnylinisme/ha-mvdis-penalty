"""Diagnostics support for Taiwan MVDIS Penalty."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import MvdisConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MvdisConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    data = entry.runtime_data.data
    return {
        "config_entry": entry.as_dict(),
        "result": {
            "count": data.count,
            "total_amount": data.total_amount,
            "checked_at": data.checked_at,
            "status": data.status,
        },
    }
