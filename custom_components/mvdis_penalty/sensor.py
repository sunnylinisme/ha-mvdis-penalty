"""Sensors for Taiwan MVDIS Penalty."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MvdisConfigEntry
from .entity import MvdisEntity
from .models import MvdisData


@dataclass(frozen=True, kw_only=True)
class MvdisSensorDescription(SensorEntityDescription):
    """Describe an MVDIS sensor."""

    value_fn: Callable[[MvdisData], Any]


SENSORS = (
    MvdisSensorDescription(
        key="unpaid_count",
        translation_key="unpaid_count",
        icon="mdi:ticket-confirmation-outline",
        native_unit_of_measurement="筆",
        value_fn=lambda data: data.count,
    ),
    MvdisSensorDescription(
        key="total_amount",
        translation_key="total_amount",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="TWD",
        value_fn=lambda data: data.total_amount,
    ),
    MvdisSensorDescription(
        key="last_check",
        translation_key="last_check",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.checked_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvdisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up MVDIS sensors."""
    async_add_entities(MvdisSensor(entry, description) for description in SENSORS)


class MvdisSensor(MvdisEntity, SensorEntity):
    """One MVDIS sensor."""

    entity_description: MvdisSensorDescription

    def __init__(
        self, entry: MvdisConfigEntry, description: MvdisSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose summaries only on the count sensor."""
        if self.entity_description.key != "unpaid_count":
            return None
        return {
            "penalties": [
                {"key": item.key, "summary": item.summary, "amount": item.amount}
                for item in self.coordinator.data.penalties
            ]
        }
