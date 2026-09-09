"""Webhook helpers for HTTP Data Bridge."""

from __future__ import annotations

from http import HTTPStatus
import json
from typing import Any

from aiohttp.web import Request, Response, json_response

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError

from .const import CONF_CLOUDHOOK_URL, CONF_LOCAL_ONLY, CONF_WEBHOOK_ID, MAX_PAYLOAD_BYTES
from .helpers import JsonValue, parse_json


class PayloadValidationError(Exception):
    """A webhook request did not contain an acceptable JSON payload."""

    def __init__(self, error: str, status: HTTPStatus) -> None:
        super().__init__(error)
        self.error = error
        self.status = status


def _get_cloud_component() -> Any:
    """Return Home Assistant Cloud without importing it during integration import.

    Home Assistant Cloud pulls in a broad optional dependency graph. HTTP Data
    Bridge only needs it when Cloud is already loaded and a source can use a
    cloudhook, so keep that import behind the runtime availability check.
    """
    from homeassistant.components import cloud  # noqa: PLC0415

    return cloud


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

    if "cloud" not in hass.config.components:
        return _generate_ha_url(hass, webhook_id, local_only=False), None, False

    cloud = _get_cloud_component()
    cloud_available = cloud.async_active_subscription(hass)
    stored_cloudhook = source_data.get(CONF_CLOUDHOOK_URL)

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

    cloud = _get_cloud_component()
    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except (cloud.CloudNotAvailable, ValueError):
        return False
    return True


async def async_read_json_payload(request: Request) -> JsonValue:
    """Read and strictly validate a bounded JSON webhook body."""
    if request.content_length is not None and request.content_length > MAX_PAYLOAD_BYTES:
        raise PayloadValidationError(
            "payload_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        )

    raw = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        raw.extend(chunk)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise PayloadValidationError(
                "payload_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            )

    try:
        return parse_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as err:
        raise PayloadValidationError("invalid_json", HTTPStatus.BAD_REQUEST) from err


def payload_error_response(err: PayloadValidationError) -> Response:
    """Convert a payload validation failure into the public webhook response."""
    return json_response({"ok": False, "error": err.error}, status=err.status)


async def async_handle_payload_request(runtime: Any, request: Request) -> Response:
    """Validate one webhook request and pass its JSON payload to a source runtime."""
    try:
        payload = await async_read_json_payload(request)
    except PayloadValidationError as err:
        return payload_error_response(err)

    await runtime.async_accept_payload(payload)
    return json_response({"ok": True})
