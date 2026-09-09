"""Shared entity base for HTTP Data Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, FIELD_NAME, FIELD_PATH
from .data import HttpDataBridgeRuntime


class HttpDataBridgeEntity(Entity):
    """Base class for entities backed by a push source."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: HttpDataBridgeRuntime,
        field: dict[str, Any],
    ) -> None:
        """Initialize the entity."""
        self._runtime = runtime
        self._field = field
        self._path = str(field[FIELD_PATH])

        self._attr_name = str(field[FIELD_NAME])
        self._attr_unique_id = f"{runtime.source_id}:{self._path}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.source_id)},
            name=runtime.subentry.title,
            manufacturer="HTTP Data Bridge",
            model="Push source",
        )

    @property
    def available(self) -> bool:
        """Return whether this selected value is fresh and present."""
        return self._runtime.available and self._path in self._runtime.values

    def _value(self):
        """Return current selected value."""
        if self._path not in self._runtime.values:
            raise KeyError(self._path)
        return self._runtime.values[self._path]

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self.async_on_remove(
            self._runtime.async_add_listener(self._async_handle_runtime_update)
        )

    def _async_handle_runtime_update(self) -> None:
        """Write changed state."""
        self.async_write_ha_state()
