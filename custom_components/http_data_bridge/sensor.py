"""Sensor platform for HTTP Data Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_FIELDS,
    DOMAIN,
    FIELD_PLATFORM,
    FIELD_UNIT,
    PLATFORM_SENSOR,
)
from .data import HttpDataBridgeManager, HttpDataBridgeRuntime
from .entity import HttpDataBridgeEntity
from .helpers import normalise_sensor_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every configured push-source subentry."""
    manager: HttpDataBridgeManager = entry.runtime_data

    for runtime in manager.sources.values():
        fields = [
            field
            for field in runtime.subentry.data.get(CONF_FIELDS, [])
            if field.get(FIELD_PLATFORM) == PLATFORM_SENSOR
        ]
        entities: list[SensorEntity] = [
            HttpDataBridgeSensor(runtime, field) for field in fields
        ]
        entities.append(HttpDataBridgeLastReceivedSensor(runtime))
        async_add_entities(
            entities,
            config_subentry_id=runtime.subentry.subentry_id,
        )


class HttpDataBridgeSensor(HttpDataBridgeEntity, SensorEntity):
    """Represent one selected JSON scalar as a sensor."""

    def __init__(
        self,
        runtime: HttpDataBridgeRuntime,
        field: dict[str, Any],
    ) -> None:
        """Initialize sensor."""
        super().__init__(runtime, field)
        unit = field.get(FIELD_UNIT)
        self._requires_number = bool(unit)
        if unit:
            self._attr_native_unit_of_measurement = str(unit)

    @property
    def available(self) -> bool:
        """Return whether a fresh HA-compatible value is present."""
        if not super().available:
            return False
        try:
            raw_value = self._value()
        except KeyError:
            return False

        if self._requires_number and not (
            isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
        ):
            return False

        value = normalise_sensor_value(raw_value)
        if value is not None and len(str(value)) > MAX_LENGTH_STATE_STATE:
            return False
        return True

    @property
    def native_value(self) -> str | int | float | None:
        """Return latest value."""
        try:
            return normalise_sensor_value(self._value())
        except KeyError:
            return None


class HttpDataBridgeLastReceivedSensor(SensorEntity):
    """Diagnostic timestamp for the last accepted webhook payload."""

    _attr_has_entity_name = True
    _attr_name = "Last received"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(self, runtime: HttpDataBridgeRuntime) -> None:
        """Initialize diagnostic sensor."""
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.source_id}:last_received"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.source_id)},
            name=runtime.subentry.title,
            manufacturer="HTTP Data Bridge",
            model="Push source",
        )

    @property
    def native_value(self):
        """Return the last receive time."""
        return self._runtime.last_received

    @property
    def available(self) -> bool:
        """Timestamp is available once a payload exists and the source is enabled."""
        return self._runtime.enabled and self._runtime.last_received is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self.async_on_remove(
            self._runtime.async_add_listener(self._async_handle_runtime_update)
        )

    def _async_handle_runtime_update(self) -> None:
        """Write changed state."""
        self.async_write_ha_state()
