"""Lifecycle cleanup tests for frontend management drafts."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.http_data_bridge.const import DOMAIN


async def _ws_call(ws_client, payload: dict) -> dict:
    """Send one WebSocket command and return the full response."""
    await ws_client.send_json_auto_id(payload)
    return await ws_client.receive_json()


async def _webhook_ids(ws_client) -> set[str]:
    """Return currently registered webhook ids through HA's public admin API."""
    message = await _ws_call(ws_client, {"type": "webhook/list"})
    assert message["success"] is True
    return {str(item["webhook_id"]) for item in message["result"]}


async def test_parent_removal_cleans_live_capture_draft(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """Deleting the integration should remove draft ingress and its expiry timer."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    with patch(
        "custom_components.http_data_bridge.frontend_management.webhook.async_generate_id",
        return_value="remove-cleanup-capture-webhook",
    ):
        started = await _ws_call(
            ws_client,
            {
                "type": f"{DOMAIN}/source/capture/start",
                "source_name": "Removal cleanup",
                "local_only": True,
            },
        )

    assert started["success"] is True
    assert "remove-cleanup-capture-webhook" in await _webhook_ids(ws_client)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert "remove-cleanup-capture-webhook" not in await _webhook_ids(ws_client)
