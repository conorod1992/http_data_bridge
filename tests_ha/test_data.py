"""Runtime data lifecycle tests for HTTP Data Bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.http_data_bridge.const import (
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_STORE_IN_ATTRIBUTE,
    PLATFORM_SENSOR,
)
from custom_components.http_data_bridge.data import HttpDataBridgeRuntime


async def _runtime_from_entry(hass: HomeAssistant, entry_factory) -> HttpDataBridgeRuntime:
    entry = entry_factory()
    subentry = entry.subentries["test-source-subentry"]
    return HttpDataBridgeRuntime(hass, entry, subentry)


async def test_shutdown_flushes_current_selected_view(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Unload should consume pending writes before replacement runtime loads."""
    runtime = await _runtime_from_entry(hass, entry_factory)
    runtime.values = {"/temperature": 21.4}
    runtime.last_received = dt_util.utcnow()

    with patch.object(runtime._store, "async_save", new_callable=AsyncMock) as save:
        await runtime.async_shutdown()

    save.assert_awaited_once()
    payload = save.await_args.args[0]
    assert payload["values"] == {"/temperature": 21.4}
    assert payload["last_received"] == runtime.last_received.isoformat()


async def test_shutdown_without_received_data_does_not_create_storage(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """An unused source should not create an empty storage file merely on unload."""
    runtime = await _runtime_from_entry(hass, entry_factory)

    with patch.object(runtime._store, "async_save", new_callable=AsyncMock) as save:
        await runtime.async_shutdown()

    save.assert_not_awaited()


async def test_selected_values_restore_after_parent_reload(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Stable source IDs should restore selected data after parent reload."""
    entry = entry_factory()
    subentry = entry.subentries["test-source-subentry"]
    runtime = HttpDataBridgeRuntime(hass, entry, subentry)
    await runtime.async_accept_payload({"temperature": 22.5, "online": True})
    received_at = runtime.last_received
    assert received_at is not None
    await runtime.async_shutdown()

    replacement = HttpDataBridgeRuntime(hass, entry, subentry)
    await replacement.async_load()
    assert replacement.values == {"/temperature": 22.5, "/online": True}
    assert replacement.last_received == received_at
    await replacement.async_shutdown()


async def test_attribute_backed_structured_value_restores_after_reload(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Only explicitly mapped structured JSON should survive a runtime reload."""
    fields = [
        {
            FIELD_PATH: "/details",
            FIELD_NAME: "Details",
            FIELD_PLATFORM: PLATFORM_SENSOR,
            FIELD_STORE_IN_ATTRIBUTE: True,
        }
    ]
    entry = entry_factory(fields=fields)
    subentry = entry.subentries["test-source-subentry"]
    runtime = HttpDataBridgeRuntime(hass, entry, subentry)
    await runtime.async_accept_payload(
        {
            "details": {"status": "ok", "items": [1, 2, 3]},
            "unmapped": {"secret": "do not persist"},
        }
    )
    received_at = runtime.last_received
    await runtime.async_shutdown()

    replacement = HttpDataBridgeRuntime(hass, entry, subentry)
    await replacement.async_load()
    assert replacement.values == {
        "/details": {"status": "ok", "items": [1, 2, 3]}
    }
    assert replacement.last_received == received_at
    await replacement.async_shutdown()


async def test_disabled_source_is_unavailable_even_with_restored_data(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Source-level disable semantics should survive consolidation into one parent."""
    runtime = await _runtime_from_entry(
        hass,
        lambda **kwargs: entry_factory(enabled=False, **kwargs),
    )
    runtime.values = {"/temperature": 21.4}
    runtime.last_received = dt_util.utcnow()
    assert runtime.available is False
