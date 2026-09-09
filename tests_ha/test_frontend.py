"""Dedicated frontend and WebSocket API tests for HTTP Data Bridge."""

from __future__ import annotations

import json

from aiohttp.test_utils import TestClient

from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant

from custom_components.http_data_bridge.const import (
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_STORE_IN_ATTRIBUTE,
    FIELD_UNIT,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
)


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
    assert by_path["/temperature"]["attribute_backed"] is False
    assert by_path["/online"]["value"] is True
    assert "must-not-appear" not in json.dumps(result)


async def test_sources_api_matches_actual_entity_type_and_length_availability(
    hass: HomeAssistant,
    hass_client,
    hass_ws_client,
    entry_factory,
) -> None:
    """The panel must not call values available when their HA entities reject them."""
    fields = [
        {
            FIELD_PATH: "/temperature",
            FIELD_NAME: "Temperature",
            FIELD_PLATFORM: PLATFORM_SENSOR,
            FIELD_UNIT: "°C",
        },
        {
            FIELD_PATH: "/online",
            FIELD_NAME: "Online",
            FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
        },
        {
            FIELD_PATH: "/message",
            FIELD_NAME: "Message",
            FIELD_PLATFORM: PLATFORM_SENSOR,
        },
    ]
    entry = entry_factory(local_only=True, fields=fields)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client: TestClient = await hass_client()
    response = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={
            "temperature": "warm",
            "online": "yes",
            "message": "x" * (MAX_LENGTH_STATE_STATE + 1),
        },
    )
    assert response.status == 200
    await hass.async_block_till_done()

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json_auto_id({"type": f"{DOMAIN}/sources"})
    message = await ws_client.receive_json()
    assert message["success"] is True

    by_path = {
        field["path"]: field for field in message["result"]["sources"][0]["fields"]
    }
    for path in ("/temperature", "/online", "/message"):
        assert by_path[path]["available"] is False
        assert by_path[path]["value"] is None


async def test_attribute_value_is_omitted_from_refresh_and_fetched_on_demand(
    hass: HomeAssistant,
    hass_client,
    hass_ws_client,
    entry_factory,
) -> None:
    """Large attribute values should cross WebSocket only when an admin requests them."""
    fields = [
        {
            FIELD_PATH: "/details",
            FIELD_NAME: "Details",
            FIELD_PLATFORM: PLATFORM_SENSOR,
            FIELD_STORE_IN_ATTRIBUTE: True,
        }
    ]
    entry = entry_factory(local_only=True, fields=fields)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    large_value = {"text": "x" * 5000, "items": [1, 2, 3]}
    client: TestClient = await hass_client()
    response = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={"details": large_value, "unmapped": "do not surface"},
    )
    assert response.status == 200
    await hass.async_block_till_done()

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json_auto_id({"type": f"{DOMAIN}/sources"})
    message = await ws_client.receive_json()
    assert message["success"] is True
    field = message["result"]["sources"][0]["fields"][0]
    assert field["attribute_backed"] is True
    assert field["available"] is True
    assert field["value"] is None
    assert "x" * 100 not in json.dumps(message["result"])

    await ws_client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/attribute_value",
            "source_id": "test-source-id",
            "path": "/details",
        }
    )
    message = await ws_client.receive_json()
    assert message["success"] is True
    assert message["result"] == {"available": True, "value": large_value}


async def test_attribute_value_api_rejects_non_attribute_mapping(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """The on-demand endpoint must not become a general source-data extractor."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/attribute_value",
            "source_id": "test-source-id",
            "path": "/temperature",
        }
    )
    message = await ws_client.receive_json()
    assert message["success"] is False
    assert message["error"]["code"] == "not_found"


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
