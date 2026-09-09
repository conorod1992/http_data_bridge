"""Webhook helpers for HTTP Data Bridge."""

from __future__ import annotations

from http import HTTPStatus
import json
from typing import Any

from aiohttp.web import Request, Response, json_response

from homeassistant.components import cloud, webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError

from .const import CONF_CLOUDHOOK_URL, CONF_LOCAL_ONLY, CONF_WEBHOOK_ID, MAX_PAYLOAD_BYTES
from .helpers import JsonValue, parse_json


async def async_resolve_webhook_url(
    hass: HomeAssistant,
    source_data: dict[str, Any],
    *,
    create_cloudhook: bool = True,
) -> tuple[str, str | None, bool]:
    """Resolve the best URL for a source.

    Return ``(url, cloudhook_url, created_cloudhook)``. A stored cloudhook is only
    used when the source is not local-only and Home Assistant Cloud has an active
    subscription. If Cloud is unavailable, fall back to Home Assistant's normal
    webhook URL without failing source setup.
    """
    webhook_id = str(source_data[CONF_WEBHOOK_ID])
    local_only = bool(source_data.get(CONF_LOCAL_ONLY, False))

    if local_only:
        return _generate_ha_url(hass, webhook_id, local_only=True), None, False

    stored_cloudhook = source_data.get(CONF_CLOUDHOOK_URL)
    cloud_available = (
        "cloud" in hass.config.components and cloud.async_active_subscription(hass)
    )

    if cloud_available and isinstance(stored_cloudhook, str) and stored_cloudhook:
        return stored_cloudhook, stored_cloudhook, False

    if cloud_available and create_cloudhook and cloud.async_is_connected(hass):
        try:
            cloudhook_url = await cloud.async_get_or_create_cloudhook(hass, webhook_id)
        except cloud.CloudNotAvailable:
            pass
        else:
            return cloudhook_url, cloudhook_url, True

    return _generate_ha_url(hass, webhook_id, local_only=False), None, False


def _generate_ha_url(hass: HomeAssistant, webhook_id: str, *, local_only: bool) -> str:
    """Generate a normal Home Assistant webhook URL with a safe path fallback."""
    try:
        if local_only:
            return webhook.async_generate_url(
                hass,
                webhook_id,
                allow_external=False,
                prefer_external=False,
            )
        return webhook.async_generate_url(hass, webhook_id, prefer_external=True)
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


async def async_delete_cloudhook(hass: HomeAssistant, webhook_id: str) -> bool:
    """Best-effort deletion of a Home Assistant Cloud cloudhook."""
    if "cloud" not in hass.config.components:
        return False
    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except (cloud.CloudNotAvailable, ValueError):
        return False
    return True


async def async_handle_payload_request(runtime: Any, request: Request) -> Response:
    """Validate one webhook request and pass its JSON payload to a source runtime."""
    if request.content_length is not None and request.content_length > MAX_PAYLOAD_BYTES:
        return json_response(
            {"ok": False, "error": "payload_too_large"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    raw = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        raw.extend(chunk)
        if len(raw) > MAX_PAYLOAD_BYTES:
            return json_response(
                {"ok": False, "error": "payload_too_large"},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )

    try:
        payload: JsonValue = parse_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=HTTPStatus.BAD_REQUEST,
        )

    await runtime.async_accept_payload(payload)
    return json_response({"ok": True})
