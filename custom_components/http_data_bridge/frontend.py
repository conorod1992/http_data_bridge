"""Dedicated management frontend for HTTP Data Bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback

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
    FIELD_STORE_IN_ATTRIBUTE,
    FIELD_UNIT,
    PANEL_STATIC_URL,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    SUBENTRY_TYPE_SOURCE,
)
from .data import HttpDataBridgeManager, HttpDataBridgeRuntime
from .helpers import mapping_value_is_available, pointer_to_label
from .webhooks import async_resolve_webhook_url

_BACKEND_REGISTERED = "frontend_backend_registered"
_PANEL_REGISTERED = "frontend_panel_registered"
_PANEL_FILE = "http-data-bridge-panel.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the management API and, when configured, the sidebar panel."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if not domain_data.get(_BACKEND_REGISTERED):
        websocket_api.async_register_command(hass, websocket_sources)
        websocket_api.async_register_command(hass, websocket_attribute_value)

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
        domain_data[_BACKEND_REGISTERED] = True

    # Do not leave a sidebar item behind merely because the integration module
    # was loaded to show its config flow. async_setup_entry calls this again once
    # the first parent entry actually exists.
    if not hass.config_entries.async_entries(DOMAIN):
        return

    # The sidebar is an optional presentation layer. Normal Home Assistant
    # installations have frontend loaded, while headless/test installations may
    # intentionally omit the separate hass_frontend package. Do not make the
    # push-data integration itself depend on that package.
    if "frontend" not in hass.config.components or domain_data.get(_PANEL_REGISTERED):
        return

    # Import lazily because panel_custom imports frontend at module import time.
    from homeassistant.components import panel_custom  # noqa: PLC0415

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
    domain_data[_PANEL_REGISTERED] = True


@callback
def async_remove_frontend_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the single parent entry is deleted."""
    domain_data = hass.data.get(DOMAIN)
    if (
        not domain_data
        or not domain_data.get(_PANEL_REGISTERED)
        or "frontend" not in hass.config.components
    ):
        return

    from homeassistant.components import frontend  # noqa: PLC0415

    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    domain_data[_PANEL_REGISTERED] = False


def _parent_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the single configured parent entry defensively."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return None
    return next((item for item in entries if item.version >= 2), entries[0])


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
        attribute_backed = bool(field.get(FIELD_STORE_IN_ATTRIBUTE, False))
        field_available = bool(
            runtime
            and runtime.available
            and value_present
            and mapping_value_is_available(field, runtime.values[path])
        )
        fields.append(
            {
                "name": str(field[FIELD_NAME]),
                "path": path,
                "path_label": pointer_to_label(path),
                "platform": str(field[FIELD_PLATFORM]),
                "unit": str(field.get(FIELD_UNIT, "")),
                "attribute_backed": attribute_backed,
                "available": field_available,
                # Attribute-backed values can approach the full 256 KiB webhook
                # limit. Keep routine panel refreshes lightweight and fetch them
                # only when an admin explicitly asks to view one.
                "value": (
                    runtime.values[path]
                    if field_available and runtime and not attribute_backed
                    else None
                ),
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
    entry = _parent_entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"entry_id": None, "sources": []})
        return

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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/attribute_value",
        vol.Required("source_id"): str,
        vol.Required("path"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_attribute_value(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one selected attribute-backed value only on explicit admin request."""
    entry = _parent_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "HTTP Data Bridge is not configured")
        return

    source_id = str(msg["source_id"])
    path = str(msg["path"])
    subentry = next(
        (
            item
            for item in entry.subentries.values()
            if item.subentry_type == SUBENTRY_TYPE_SOURCE
            and str(item.data.get(CONF_SOURCE_ID, "")) == source_id
        ),
        None,
    )
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "Push source not found")
        return

    mapping = next(
        (
            field
            for field in subentry.data.get(CONF_FIELDS, [])
            if str(field.get(FIELD_PATH, "")) == path
            and bool(field.get(FIELD_STORE_IN_ATTRIBUTE, False))
        ),
        None,
    )
    if mapping is None:
        connection.send_error(msg["id"], "not_found", "Attribute-backed mapping not found")
        return

    manager = getattr(entry, "runtime_data", None)
    runtime = (
        manager.sources.get(subentry.subentry_id)
        if isinstance(manager, HttpDataBridgeManager)
        else None
    )
    if (
        runtime is None
        or not runtime.available
        or path not in runtime.values
        or not mapping_value_is_available(mapping, runtime.values[path])
    ):
        connection.send_result(msg["id"], {"available": False, "value": None})
        return

    connection.send_result(
        msg["id"],
        {"available": True, "value": runtime.values[path]},
    )
