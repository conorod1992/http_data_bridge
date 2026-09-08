"""Runtime data handling for HTTP Data Bridge."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FIELDS,
    CONF_STALE_AFTER,
    DOMAIN,
    FIELD_PATH,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .helpers import JsonValue, get_by_pointer

UpdateListener = Callable[[], None]


def _storage(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Create the storage helper for one config entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


async def async_remove_storage(hass: HomeAssistant, entry_id: str) -> None:
    """Remove persisted data for a deleted config entry."""
    await _storage(hass, entry_id).async_remove()


class HttpDataBridgeRuntime:
    """Own one push source's selected values and availability state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize runtime data."""
        self.hass = hass
        self.entry = entry
        self.values: dict[str, JsonValue] = {}
        self.last_received: datetime | None = None

        self._listeners: set[UpdateListener] = set()
        self._cancel_stale_timer: Callable[[], None] | None = None
        self._store = _storage(hass, entry.entry_id)

    @property
    def stale_after(self) -> int:
        """Return stale timeout in seconds; zero disables expiry."""
        value = self.entry.data.get(CONF_STALE_AFTER, 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @property
    def available(self) -> bool:
        """Return whether the source currently has fresh data."""
        if self.last_received is None:
            return False

        stale_after = self.stale_after
        if stale_after <= 0:
            return True

        age = (dt_util.utcnow() - self.last_received).total_seconds()
        return age < stale_after

    async def async_load(self) -> None:
        """Load the last selected values from storage."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return

        configured_paths = {
            str(field[FIELD_PATH]) for field in self.entry.data.get(CONF_FIELDS, [])
        }

        stored_values = stored.get("values")
        if isinstance(stored_values, dict):
            self.values = {
                str(path): value
                for path, value in stored_values.items()
                if str(path) in configured_paths
            }

        raw_last_received = stored.get("last_received")
        if isinstance(raw_last_received, str):
            parsed = dt_util.parse_datetime(raw_last_received)
            if parsed is not None:
                self.last_received = dt_util.as_utc(parsed)

        # Re-save the filtered view so fields removed by reconfiguration do not
        # linger on disk indefinitely.
        if isinstance(stored_values, dict) and set(stored_values) != set(self.values):
            self._store.async_delay_save(self._storage_payload, STORAGE_SAVE_DELAY)

        self._schedule_stale_timer()

    async def async_accept_payload(self, payload: JsonValue) -> None:
        """Extract configured values from a new payload and persist only those."""
        new_values: dict[str, JsonValue] = {}
        for field in self.entry.data.get(CONF_FIELDS, []):
            path = str(field[FIELD_PATH])
            try:
                value = get_by_pointer(payload, path)
            except KeyError:
                continue
            if isinstance(value, (dict, list)):
                # A configured scalar changed shape. Treat it as missing instead of
                # serializing arbitrary nested data into entity state/storage.
                continue
            new_values[path] = value

        self.values = new_values
        self.last_received = dt_util.utcnow()

        self._store.async_delay_save(self._storage_payload, STORAGE_SAVE_DELAY)

        self._schedule_stale_timer()
        self._notify_listeners()

    def _storage_payload(self) -> dict[str, Any]:
        """Return the minimal data persisted for this source."""
        return {
            "values": self.values,
            "last_received": (
                self.last_received.isoformat() if self.last_received is not None else None
            ),
        }

    @callback
    def async_add_listener(self, listener: UpdateListener) -> Callable[[], None]:
        """Subscribe to payload/availability changes."""
        self._listeners.add(listener)

        @callback
        def _remove() -> None:
            self._listeners.discard(listener)

        return _remove

    async def async_shutdown(self) -> None:
        """Cancel callbacks and consume any pending delayed storage write."""
        if self._cancel_stale_timer is not None:
            self._cancel_stale_timer()
            self._cancel_stale_timer = None

        # A delayed Store callback belongs to this runtime's Store instance and
        # can otherwise fire after a config-entry reload. Flush the current
        # selected view now so an old runtime cannot re-persist removed mappings
        # after the replacement runtime has loaded.
        if self.last_received is not None or self.values:
            await self._store.async_save(self._storage_payload())

    @callback
    def _notify_listeners(self) -> None:
        """Notify entity listeners."""
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _schedule_stale_timer(self) -> None:
        """Schedule the exact point at which the source becomes stale."""
        if self._cancel_stale_timer is not None:
            self._cancel_stale_timer()
            self._cancel_stale_timer = None

        if self.last_received is None or self.stale_after <= 0:
            return

        age = (dt_util.utcnow() - self.last_received).total_seconds()
        remaining = self.stale_after - age
        if remaining <= 0:
            return

        self._cancel_stale_timer = async_call_later(
            self.hass,
            remaining,
            self._async_stale,
        )

    @callback
    def _async_stale(self, _now: datetime) -> None:
        """Notify entities when the source becomes stale."""
        self._cancel_stale_timer = None
        self._notify_listeners()
