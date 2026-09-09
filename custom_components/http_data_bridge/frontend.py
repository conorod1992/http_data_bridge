"""Dedicated management frontend for HTTP Data Bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENABLED,
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SOURCE_ID,
    CONF_STALE_AFTER,
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_UNIT,
    PANEL_STATIC_URL,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    SUBENTRY_TYPE_SOURCE,
)
from .data import HttpDataBridgeManager, HttpDataBridgeRuntime
from .helpers import pointer_to_label
from .webhooks import async_resolve_webhook_url

_FRONTEND_REGISTERED = "frontend_registered"
_PANEL_FILE = "http-data-bridge-panel.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the admin-only panel and its WebSocket API once per HA boot."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_FRONTEND_REGISTERED):
        return

    websocket_api.async_register_command(hass, websocket_sources)

    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_STATIC_URL,
                str(frontend_dir),
                cache_headers=False,
            )
        ]
    )
    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        sidebar_title="HTTP Data Bridge",
        sidebar_icon="mdi:webhook",
        module_url=f"{PANEL_STATIC_URL}/{_PANEL_FILE}",
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    domain_data[_FRONTEND_REGISTERED] = True


async def _source_snapshot(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
) -> dict[str, Any]:
    """Build the admin frontend representation of one configured source."""
    manager = getattr(entry, "runtime_data", None)
    runtime: HttpDataBridgeRuntime | None = None
    if isinstance(manager, HttpDataBridgeManager):
        runtime = manager.sources.get(subentry.subentry_id)

    enabled = bool(subentry.data.get(CONF_ENABLED, True))
    local_only = bool(subentry.data.get(CONF_LOCAL_ONLY, False))
    stale_after = max(0, int(subentry.data.get(CONF_STALE_AFTER, 0) or 0))

    webhook_url, cloudhook_url, _created = await async_resolve_webhook_url(
        hass,
        dict(subentry.data),
        create_cloudhook=False,
    )

    if not enabled:
        status = "disabled"
    elif runtime is None:
        status = "unloaded"
    elif runtime.last_received is None:
        status = "waiting"
    elif runtime.available:
        status = "available"
    else:
        status = "stale"

    fields: list[dict[str, Any]] = []
    for field in subentry.data.get(CONF_FIELDS, []):
        path = str(field[FIELD_PATH])
        value_present = runtime is not None and path in runtime.values
        fields.append(
            {
                "name": str(field[FIELD_NAME]),
                "path": path,
                "path_label": pointer_to_label(path),
                "platform": str(field[FIELD_PLATFORM]),
                "unit": str(field.get(FIELD_UNIT, "")),
                "available": bool(runtime and runtime.available and value_present),
                "value": runtime.values[path] if value_present and runtime else None,
            }
        )

    return {
        "subentry_id": subentry.subentry_id,
        "source_id": str(subentry.data.get(CONF_SOURCE_ID, "")),
        "name": subentry.title,
        "enabled": enabled,
        "local_only": local_only,
        "stale_after": stale_after,
        "status": status,
        "last_received": (
            runtime.last_received.isoformat()
            if runtime is not None and runtime.last_received is not None
            else None
        ),
        "webhook_url": webhook_url,
        "uses_cloudhook": bool(cloudhook_url),
        "fields": fields,
    }


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/sources"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_sources(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return configured sources and selected values for the admin panel."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_result(msg["id"], {"entry_id": None, "sources": []})
        return

    # The integration enforces one parent entry. Keep the API defensive in case
    # old/incomplete migration data temporarily leaves more than one entry.
    entry = next((item for item in entries if item.version >= 2), entries[0])
    sources = [
        await _source_snapshot(hass, entry, subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SOURCE
    ]
    sources.sort(key=lambda item: item["name"].casefold())
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "sources": sources,
        },
    )
