"""Binary sensor platform for HTTP Data Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_FIELDS, FIELD_PLATFORM, PLATFORM_BINARY_SENSOR
from .data import HttpDataBridgeRuntime
from .entity import HttpDataBridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up HTTP Data Bridge binary sensors."""
    runtime: HttpDataBridgeRuntime = entry.runtime_data
    fields = [
        field
        for field in entry.data.get(CONF_FIELDS, [])
        if field.get(FIELD_PLATFORM) == PLATFORM_BINARY_SENSOR
    ]
    async_add_entities(
        HttpDataBridgeBinarySensor(entry, runtime, field) for field in fields
    )


class HttpDataBridgeBinarySensor(HttpDataBridgeEntity, BinarySensorEntity):
    """Represent one selected JSON boolean as a binary sensor."""

    def __init__(
        self,
        entry: ConfigEntry,
        runtime: HttpDataBridgeRuntime,
        field: dict[str, Any],
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(entry, runtime, field)

    @property
    def available(self) -> bool:
        """Return whether a fresh boolean value is present."""
        if not super().available:
            return False
        try:
            return isinstance(self._value(), bool)
        except KeyError:
            return False

    @property
    def is_on(self) -> bool | None:
        """Return latest boolean value."""
        try:
            value = self._value()
        except KeyError:
            return None
        return value if isinstance(value, bool) else None
