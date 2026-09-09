"""Dedicated frontend and WebSocket API tests for HTTP Data Bridge."""

from __future__ import annotations

import json

from aiohttp.test_utils import TestClient

from homeassistant.core import HomeAssistant

from custom_components.http_data_bridge.const import DOMAIN


async def test_admin_sources_api_reports_selected_values_only(
    hass: HomeAssistant,
    hass_client,
    hass_ws_client,
    entry_factory,
) -> None:
    """The panel API should expose source health without retaining unselected payload data."""
    entry = entry_factory(local_only=True, stale_after=120)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client: TestClient = await hass_client()
    response = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={
            "temperature": 23.1,
            "online": True,
            "private_unmapped_value": "must-not-appear",
        },
    )
    assert response.status == 200
    await hass.async_block_till_done()

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json_auto_id({"type": f"{DOMAIN}/sources"})
    message = await ws_client.receive_json()

    assert message["success"] is True
    result = message["result"]
    assert result["entry_id"] == entry.entry_id
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["name"] == "Test source"
    assert source["status"] == "available"
    assert source["local_only"] is True
    assert source["stale_after"] == 120
    assert source["last_received"] is not None
    assert "test-http-data-bridge-webhook" in source["webhook_url"]

    by_path = {field["path"]: field for field in source["fields"]}
    assert by_path["/temperature"]["value"] == 23.1
    assert by_path["/online"]["value"] is True
    assert "must-not-appear" not in json.dumps(result)


async def test_sources_api_represents_disabled_source_without_runtime_webhook(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """The panel should still show a configured source that is intentionally disabled."""
    entry = entry_factory(local_only=True, enabled=False)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json_auto_id({"type": f"{DOMAIN}/sources"})
    message = await ws_client.receive_json()

    source = message["result"]["sources"][0]
    assert source["enabled"] is False
    assert source["status"] == "disabled"
    assert all(field["available"] is False for field in source["fields"])
