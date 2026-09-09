"""Full frontend source-management contract tests for HTTP Data Bridge."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

from aiohttp.test_utils import TestClient

from homeassistant.core import HomeAssistant

from custom_components.http_data_bridge.const import (
    CONF_ENABLED,
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SOURCE_ID,
    CONF_SOURCE_NAME,
    CONF_STALE_AFTER,
    CONF_WEBHOOK_ID,
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_STORE_IN_ATTRIBUTE,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
)


async def _ws_call(ws_client, payload: dict) -> dict:
    """Send one WebSocket command and return the full response."""
    await ws_client.send_json_auto_id(payload)
    return await ws_client.receive_json()


async def test_prepare_pasted_sample_returns_mapping_metadata(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """Pasted JSON should become a transient draft with useful, bounded node metadata."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    message = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/prepare_sample",
            "sample_payload": '{"details":{"message":"hello"},"online":true}',
        },
    )
    assert message["success"] is True
    result = message["result"]
    assert result["draft_id"]
    nodes = {node["path"]: node for node in result["nodes"]}
    assert nodes[""]["kind"] == "object"
    assert nodes[""]["requires_attribute"] is True
    assert nodes["/details"]["kind"] == "object"
    assert nodes["/details/message"]["preview"] == "hello"
    assert nodes["/online"]["is_boolean"] is True

    cancel = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/draft/cancel",
            "draft_id": result["draft_id"],
        },
    )
    assert cancel["success"] is True


async def test_prepare_null_root_sample_is_not_confused_with_missing_sample(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """JSON null is a valid root sample and must remain selectable."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    prepared = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/prepare_sample",
            "sample_payload": "null",
        },
    )
    assert prepared["success"] is True
    assert prepared["result"]["nodes"] == [
        {
            "path": "",
            "label": "$",
            "preview": "null",
            "suggested_name": "Payload",
            "kind": "null",
            "is_boolean": False,
            "is_number": False,
            "requires_attribute": False,
        }
    ]


async def test_panel_can_add_source_from_pasted_sample(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """The panel should create a canonical ConfigSubentry with validated mappings."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    prepared = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/prepare_sample",
            "sample_payload": '{"temperature":21.4,"online":true,"details":{"a":1}}',
        },
    )
    draft_id = prepared["result"]["draft_id"]

    saved = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/save",
            "name": "Panel source",
            "enabled": True,
            "local_only": True,
            "stale_after": 90,
            "replace_mappings": True,
            "draft_id": draft_id,
            "fields": [
                {
                    FIELD_PATH: "/temperature",
                    FIELD_NAME: "Temperature",
                    FIELD_PLATFORM: PLATFORM_SENSOR,
                    "unit": "°C",
                },
                {
                    FIELD_PATH: "/online",
                    FIELD_NAME: "Online",
                    FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
                },
                {
                    FIELD_PATH: "/details",
                    FIELD_NAME: "Details",
                    FIELD_PLATFORM: PLATFORM_SENSOR,
                    FIELD_STORE_IN_ATTRIBUTE: True,
                },
            ],
        },
    )
    assert saved["success"] is True
    assert saved["result"]["created"] is True
    source_id = saved["result"]["source_id"]
    assert source_id
    assert "/api/webhook/" in saved["result"]["webhook_url"]

    await hass.async_block_till_done()
    source = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data.get(CONF_SOURCE_ID) == source_id
    )
    assert source.title == "Panel source"
    assert source.data[CONF_SOURCE_NAME] == "Panel source"
    assert source.data[CONF_STALE_AFTER] == 90
    assert source.data[CONF_LOCAL_ONLY] is True
    assert source.data[CONF_ENABLED] is True
    assert source.data[CONF_WEBHOOK_ID]
    assert source.data[CONF_FIELDS][2][FIELD_STORE_IN_ATTRIBUTE] is True


async def test_panel_rejects_mapping_incompatible_with_sample(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """Frontend callers cannot bypass the same entity-type/storage safety rules."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    prepared = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/prepare_sample",
            "sample_payload": '{"status":"running","details":{"a":1}}',
        },
    )
    draft_id = prepared["result"]["draft_id"]

    bad_binary = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/save",
            "name": "Invalid",
            "enabled": True,
            "local_only": True,
            "stale_after": 0,
            "replace_mappings": True,
            "draft_id": draft_id,
            "fields": [
                {
                    FIELD_PATH: "/status",
                    FIELD_NAME: "Status",
                    FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
                }
            ],
        },
    )
    assert bad_binary["success"] is False
    assert bad_binary["error"]["code"] == "invalid_mapping"

    bad_container = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/save",
            "name": "Invalid",
            "enabled": True,
            "local_only": True,
            "stale_after": 0,
            "replace_mappings": True,
            "draft_id": draft_id,
            "fields": [
                {
                    FIELD_PATH: "/details",
                    FIELD_NAME: "Details",
                    FIELD_PLATFORM: PLATFORM_SENSOR,
                }
            ],
        },
    )
    assert bad_container["success"] is False
    assert bad_container["error"]["code"] == "invalid_mapping"


async def test_panel_edit_keep_mappings_preserves_source_and_webhook_identity(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """Simple frontend edits should not churn mappings, source ID, or webhook secret."""
    entry = entry_factory(local_only=True)
    source = entry.subentries["test-source-subentry"]
    old_fields = list(source.data[CONF_FIELDS])
    old_webhook_id = source.data[CONF_WEBHOOK_ID]
    old_source_id = source.data[CONF_SOURCE_ID]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    saved = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/save",
            "source_id": old_source_id,
            "name": "Renamed from panel",
            "enabled": False,
            "local_only": True,
            "stale_after": 300,
            "replace_mappings": False,
            "fields": [],
        },
    )
    assert saved["success"] is True
    assert saved["result"]["created"] is False
    await hass.async_block_till_done()

    updated = entry.subentries[source.subentry_id]
    assert updated.title == "Renamed from panel"
    assert updated.data[CONF_SOURCE_ID] == old_source_id
    assert updated.data[CONF_WEBHOOK_ID] == old_webhook_id
    assert updated.data[CONF_FIELDS] == old_fields
    assert updated.data[CONF_ENABLED] is False
    assert updated.data[CONF_STALE_AFTER] == 300


async def test_panel_live_capture_uses_temporary_endpoint_and_cancel_removes_it(
    hass: HomeAssistant,
    hass_client,
    hass_ws_client,
    entry_factory,
) -> None:
    """Live discovery from the panel should be temporary and explicitly cleanable."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    with patch(
        "custom_components.http_data_bridge.frontend_management.webhook.async_generate_id",
        return_value="panel-capture-webhook",
    ):
        started = await _ws_call(
            ws_client,
            {
                "type": f"{DOMAIN}/source/capture/start",
                "source_name": "Panel capture",
                "local_only": True,
            },
        )
    assert started["success"] is True
    draft_id = started["result"]["draft_id"]
    assert "panel-capture-webhook" in started["result"]["capture_url"]

    client: TestClient = await hass_client()
    response = await client.post(
        "/api/webhook/panel-capture-webhook",
        json={"message": "hello", "online": True},
    )
    assert response.status == HTTPStatus.OK

    status = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/capture/status",
            "draft_id": draft_id,
        },
    )
    assert status["success"] is True
    assert status["result"]["captured"] is True
    assert {node["path"] for node in status["result"]["nodes"]} >= {
        "",
        "/message",
        "/online",
    }

    cancelled = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/draft/cancel",
            "draft_id": draft_id,
        },
    )
    assert cancelled["success"] is True
    response = await client.post(
        "/api/webhook/panel-capture-webhook",
        json={"message": "should fail"},
    )
    assert response.status == HTTPStatus.NOT_FOUND


async def test_panel_delete_removes_source_and_unregisters_webhook(
    hass: HomeAssistant,
    hass_client,
    hass_ws_client,
    entry_factory,
) -> None:
    """Deleting from the panel should use normal subentry cleanup and remove ingress."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)
    client: TestClient = await hass_client()

    before = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={"temperature": 20, "online": True},
    )
    assert before.status == HTTPStatus.OK

    deleted = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/delete",
            "source_id": "test-source-id",
        },
    )
    assert deleted["success"] is True
    await hass.async_block_till_done()
    assert "test-source-subentry" not in entry.subentries

    after = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={"temperature": 21, "online": True},
    )
    assert after.status == HTTPStatus.NOT_FOUND
