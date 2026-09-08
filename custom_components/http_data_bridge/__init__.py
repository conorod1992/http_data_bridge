"""HTTP Data Bridge integration."""

from __future__ import annotations

from http import HTTPStatus
import json

from aiohttp.web import Request, Response, json_response

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_LOCAL_ONLY,
    CONF_WEBHOOK_ID,
    DOMAIN,
    MAX_PAYLOAD_BYTES,
)
from .data import HttpDataBridgeRuntime, async_remove_storage
from .helpers import JsonValue, parse_json

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HTTP Data Bridge from a config entry."""
    runtime = HttpDataBridgeRuntime(hass, entry)
    await runtime.async_load()
    entry.runtime_data = runtime

    async def _handle_webhook(
        _hass: HomeAssistant,
        _webhook_id: str,
        request: Request,
    ) -> Response:
        """Handle a JSON payload for this config entry."""
        if (
            request.content_length is not None
            and request.content_length > MAX_PAYLOAD_BYTES
        ):
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

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        str(entry.data[CONF_WEBHOOK_ID]),
        _handle_webhook,
        local_only=bool(entry.data.get(CONF_LOCAL_ONLY, False)),
        allowed_methods={"POST"},
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        webhook.async_unregister(hass, str(entry.data[CONF_WEBHOOK_ID]))
        await runtime.async_shutdown()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an HTTP Data Bridge config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    webhook.async_unregister(hass, str(entry.data[CONF_WEBHOOK_ID]))
    runtime: HttpDataBridgeRuntime = entry.runtime_data
    await runtime.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted selected values when a config entry is deleted."""
    await async_remove_storage(hass, entry.entry_id)
