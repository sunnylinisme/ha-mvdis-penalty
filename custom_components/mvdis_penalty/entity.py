"""Base entity for Taiwan MVDIS Penalty."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MvdisConfigEntry
from .const import DOMAIN
from .coordinator import MvdisCoordinator


class MvdisEntity(CoordinatorEntity[MvdisCoordinator]):
    """Base coordinated MVDIS entity."""

    _attr_has_entity_name = True

    def __init__(self, entry: MvdisConfigEntry, key: str) -> None:
        """Initialize an entity."""
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "監理服務罰單",
            "manufacturer": "交通部公路局",
            "model": "監理服務網",
            "configuration_url": (
                "https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay"
                "?method=pagination"
            ),
        }
