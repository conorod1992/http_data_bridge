"""Nabu Casa cloudhook request compatibility tests."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

import pytest

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.util.aiohttp import MockRequest

from custom_components.http_data_bridge.const import DOMAIN, MAX_PAYLOAD_BYTES
from custom_components.http_data_bridge.webhooks import (
    PayloadValidationError,
    async_read_json_payload,
)


async def _ws_call(ws_client, payload: dict) -> dict:
    """Send one WebSocket command and return its response."""
    await ws_client.send_json_auto_id(payload)
    return await ws_client.receive_json()


async def test_frontend_live_capture_accepts_cloudhook_mockrequest(
    hass: HomeAssistant,
    hass_ws_client,
    entry_factory,
) -> None:
    """Nabu Casa MockRequest forwarding should populate a live-capture draft."""
    entry = entry_factory(local_only=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws_client = await hass_ws_client(hass)

    with patch(
        "custom_components.http_data_bridge.frontend_management.webhook.async_generate_id",
        return_value="cloudhook-capture-webhook",
    ):
        started = await _ws_call(
            ws_client,
            {
                "type": f"{DOMAIN}/source/capture/start",
                "source_name": "Cloud capture",
                "local_only": False,
            },
        )

    assert started["success"] is True
    draft_id = started["result"]["draft_id"]

    response = await webhook.async_handle_webhook(
        hass,
        "cloudhook-capture-webhook",
        MockRequest(
            content=b'{"temperature":21.4,"online":true,"message":"hello"}',
            mock_source="cloud",
            method="POST",
        ),
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
    paths = {node["path"] for node in status["result"]["nodes"]}
    assert {"", "/temperature", "/online", "/message"} <= paths

    cancelled = await _ws_call(
        ws_client,
        {
            "type": f"{DOMAIN}/source/draft/cancel",
            "draft_id": draft_id,
        },
    )
    assert cancelled["success"] is True


async def test_cloudhook_mockrequest_still_enforces_payload_limit() -> None:
    """Cloudhook requests without Content-Length remain bounded to 256 KiB."""
    request = MockRequest(
        content=b"x" * (MAX_PAYLOAD_BYTES + 1),
        mock_source="cloud",
        method="POST",
    )

    with pytest.raises(PayloadValidationError) as err:
        await async_read_json_payload(request)

    assert err.value.error == "payload_too_large"
    assert err.value.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
