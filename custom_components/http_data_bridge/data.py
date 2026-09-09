"""Runtime data handling for HTTP Data Bridge."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_ENABLED,
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SOURCE_ID,
    CONF_STALE_AFTER,
    CONF_WEBHOOK_ID,
    DOMAIN,
    FIELD_PATH,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    SUBENTRY_TYPE_SOURCE,
)
from .helpers import JsonValue, get_by_pointer
from .webhooks import (
    async_delete_cloudhook,
    async_handle_payload_request,
    async_resolve_webhook_url,
)

UpdateListener = Callable[[], None]


def _storage(hass: HomeAssistant, source_id: str) -> Store[dict[str, Any]]:
    """Create the storage helper for one stable source id."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{source_id}")


async def async_remove_storage(hass: HomeAssistant, source_id: str) -> None:
    """Remove persisted data for a deleted source."""
    await _storage(hass, source_id).async_remove()


class HttpDataBridgeManager:
    """Own all push sources belonging to one parent config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.sources: dict[str, HttpDataBridgeRuntime] = {}

    async def async_setup(self) -> None:
        """Load source state, resolve cloudhooks, and register webhooks."""
        for subentry in tuple(self.entry.subentries.values()):
            if subentry.subentry_type != SUBENTRY_TYPE_SOURCE:
                continue

            runtime = HttpDataBridgeRuntime(self.hass, self.entry, subentry)
            await runtime.async_load()
            self.sources[subentry.subentry_id] = runtime

            if not runtime.enabled:
                continue

            source_data = dict(subentry.data)
            _, cloudhook_url, created = await async_resolve_webhook_url(
                self.hass, source_data
            )
            if cloudhook_url:
                runtime.cloudhook_url = cloudhook_url
                if created or source_data.get(CONF_CLOUDHOOK_URL) != cloudhook_url:
                    source_data[CONF_CLOUDHOOK_URL] = cloudhook_url
                    self.hass.config_entries.async_update_subentry(
                        self.entry,
                        subentry,
                        data=source_data,
                    )

            webhook.async_register(
                self.hass,
                DOMAIN,
                subentry.title,
                runtime.webhook_id,
                self._handler_for(runtime),
                local_only=runtime.local_only,
                allowed_methods={"POST"},
            )
            runtime.webhook_registered = True

    def _handler_for(self, runtime: HttpDataBridgeRuntime):
        """Build the Home Assistant webhook handler for one source."""

        async def _handle_webhook(
            _hass: HomeAssistant, _webhook_id: str, request: Request
        ) -> Response:
            return await async_handle_payload_request(runtime, request)

        return _handle_webhook

    async def async_shutdown(self) -> None:
        """Unload all source runtimes and clean sources removed/restricted by update."""
        current_by_source_id = {
            str(subentry.data.get(CONF_SOURCE_ID, "")): subentry
            for subentry in self.entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SOURCE
        }

        for runtime in self.sources.values():
            if runtime.webhook_registered:
                webhook.async_unregister(self.hass, runtime.webhook_id)
                runtime.webhook_registered = False

            await runtime.async_shutdown()

            current = current_by_source_id.get(runtime.source_id)
            if current is None:
                if runtime.cloudhook_url or not runtime.local_only:
                    await async_delete_cloudhook(self.hass, runtime.webhook_id)
                await async_remove_storage(self.hass, runtime.source_id)
                continue

            # A reconfigure may switch a remotely reachable source to local-only.
            # Ensure an old cloudhook is removed even if the config-flow cleanup
            # could not reach Home Assistant Cloud at save time.
            if bool(current.data.get(CONF_LOCAL_ONLY, False)) and runtime.cloudhook_url:
                await async_delete_cloudhook(self.hass, runtime.webhook_id)


class HttpDataBridgeRuntime:
    """Own one push source's selected values and availability state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize runtime data."""
        self.hass = hass
        self.entry = entry
        self.subentry = subentry
        self.source_id = str(subentry.data[CONF_SOURCE_ID])
        self.values: dict[str, JsonValue] = {}
        self.last_received: datetime | None = None
        self.cloudhook_url = (
            str(subentry.data[CONF_CLOUDHOOK_URL])
            if subentry.data.get(CONF_CLOUDHOOK_URL)
            else None
        )
        self.webhook_registered = False

        self._listeners: set[UpdateListener] = set()
        self._cancel_stale_timer: Callable[[], None] | None = None
        self._store = _storage(hass, self.source_id)

    @property
    def enabled(self) -> bool:
        """Return whether this source should accept pushes."""
        return bool(self.subentry.data.get(CONF_ENABLED, True))

    @property
    def webhook_id(self) -> str:
        """Return the stable secret webhook id."""
        return str(self.subentry.data[CONF_WEBHOOK_ID])

    @property
    def local_only(self) -> bool:
        """Return whether the webhook only accepts local requests."""
        return bool(self.subentry.data.get(CONF_LOCAL_ONLY, False))

    @property
    def stale_after(self) -> int:
        """Return stale timeout in seconds; zero disables expiry."""
        value = self.subentry.data.get(CONF_STALE_AFTER, 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @property
    def available(self) -> bool:
        """Return whether the source currently has fresh data."""
        if not self.enabled or self.last_received is None:
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
            str(field[FIELD_PATH]) for field in self.subentry.data.get(CONF_FIELDS, [])
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

        if isinstance(stored_values, dict) and set(stored_values) != set(self.values):
            self._store.async_delay_save(self._storage_payload, STORAGE_SAVE_DELAY)

        self._schedule_stale_timer()

    async def async_accept_payload(self, payload: JsonValue) -> None:
        """Extract configured values from a new payload and persist only those."""
        new_values: dict[str, JsonValue] = {}
        for field in self.subentry.data.get(CONF_FIELDS, []):
            path = str(field[FIELD_PATH])
            try:
                value = get_by_pointer(payload, path)
            except KeyError:
                continue
            if isinstance(value, (dict, list)):
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
