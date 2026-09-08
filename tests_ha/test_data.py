"""Runtime data lifecycle tests for HTTP Data Bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.http_data_bridge.data import HttpDataBridgeRuntime


async def test_shutdown_flushes_current_selected_view(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Unload should consume pending writes before a replacement runtime can load."""
    entry = entry_factory()
    runtime = HttpDataBridgeRuntime(hass, entry)
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
    entry = entry_factory()
    runtime = HttpDataBridgeRuntime(hass, entry)

    with patch.object(runtime._store, "async_save", new_callable=AsyncMock) as save:
        await runtime.async_shutdown()

    save.assert_not_awaited()


async def test_selected_values_restore_after_runtime_reload(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """A replacement runtime should restore the last persisted selected snapshot."""
    entry = entry_factory()
    runtime = HttpDataBridgeRuntime(hass, entry)
    await runtime.async_accept_payload({"temperature": 22.5, "online": True})
    received_at = runtime.last_received
    assert received_at is not None
    await runtime.async_shutdown()

    replacement = HttpDataBridgeRuntime(hass, entry)
    await replacement.async_load()

    assert replacement.values == {"/temperature": 22.5, "/online": True}
    assert replacement.last_received == received_at
    await replacement.async_shutdown()
