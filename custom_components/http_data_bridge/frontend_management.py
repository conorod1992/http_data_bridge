"""In-panel source management for HTTP Data Bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from aiohttp.web import Request, Response, json_response
import voluptuous as vol

from homeassistant.components import webhook, websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_CLOUDHOOK_URL,
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
    FIELD_UNIT,
    MAX_PAYLOAD_BYTES,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
    SUBENTRY_TYPE_SOURCE,
)
from .helpers import (
    JsonValue,
    get_by_pointer,
    iter_json_nodes,
    mapping_value_is_available,
    parse_json,
    pointer_to_label,
    sample_display,
    suggested_name,
)
from .webhooks import (
    PayloadValidationError,
    async_delete_cloudhook,
    async_read_json_payload,
    async_resolve_webhook_url,
    payload_error_response,
)

_DRAFTS_KEY = "frontend_management_drafts"
_DRAFT_TTL_SECONDS = 15 * 60
_UNSET = object()


@dataclass
class FrontendDraft:
    """Transient sample/capture state used by the management panel."""

    draft_id: str
    sample: JsonValue | None = None
    sample_available: bool = False
    capture_webhook_id: str | None = None
    capture_cloudhook_url: str | None = None
    capture_url: str | None = None
    capture_registered: bool = False
    cancel_expiry: Callable[[], None] | None = None


def _parent_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the single configured parent entry defensively."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return None
    return next((item for item in entries if item.version >= 2), entries[0])


def _drafts(hass: HomeAssistant) -> dict[str, FrontendDraft]:
    """Return the in-memory management draft registry."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_DRAFTS_KEY, {})


def _source_by_id(entry: ConfigEntry, source_id: str) -> ConfigSubentry | None:
    """Find one push-source subentry by its stable source id."""
    return next(
        (
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_SOURCE
            and str(subentry.data.get(CONF_SOURCE_ID, "")) == source_id
        ),
        None,
    )


def _requires_attribute_storage(value: JsonValue) -> bool:
    """Return whether a value cannot be represented as a normal sensor state."""
    return not mapping_value_is_available({FIELD_PLATFORM: PLATFORM_SENSOR}, value)


def _node_kind(value: JsonValue) -> str:
    """Return a compact JSON type label for the frontend."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _sample_nodes(sample: JsonValue) -> list[dict[str, Any]]:
    """Serialize sample-node metadata without duplicating large container values."""
    nodes: list[dict[str, Any]] = []
    for path, value in iter_json_nodes(sample):
        is_boolean = isinstance(value, bool)
        is_number = isinstance(value, (int, float)) and not is_boolean
        nodes.append(
            {
                "path": path,
                "label": pointer_to_label(path),
                "preview": sample_display(value),
                "suggested_name": suggested_name(path),
                "kind": _node_kind(value),
                "is_boolean": is_boolean,
                "is_number": is_number,
                "requires_attribute": _requires_attribute_storage(value),
            }
        )
    return nodes


def _new_draft(
    hass: HomeAssistant, sample: JsonValue | object = _UNSET
) -> FrontendDraft:
    """Create a bounded-lifetime frontend management draft."""
    draft_id = uuid4().hex
    draft = FrontendDraft(
        draft_id=draft_id,
        sample=None if sample is _UNSET else sample,
        sample_available=sample is not _UNSET,
    )
    _drafts(hass)[draft_id] = draft

    @callback
    def _expire(_now: Any) -> None:
        hass.async_create_task(
            async_delete_management_draft(hass, draft_id),
            "expire HTTP Data Bridge frontend draft",
        )

    draft.cancel_expiry = async_call_later(hass, _DRAFT_TTL_SECONDS, _expire)
    return draft


async def async_delete_management_draft(hass: HomeAssistant, draft_id: str) -> None:
    """Remove one temporary draft and any capture endpoints it owns."""
    draft = _drafts(hass).pop(draft_id, None)
    if draft is None:
        return

    if draft.cancel_expiry is not None:
        draft.cancel_expiry()
        draft.cancel_expiry = None

    if draft.capture_registered and draft.capture_webhook_id:
        webhook.async_unregister(hass, draft.capture_webhook_id)
        draft.capture_registered = False

    if draft.capture_cloudhook_url and draft.capture_webhook_id:
        await async_delete_cloudhook(hass, draft.capture_webhook_id)


async def async_cleanup_management_drafts(hass: HomeAssistant) -> None:
    """Clean all temporary panel drafts, for example when the integration is removed."""
    for draft_id in tuple(_drafts(hass)):
        await async_delete_management_draft(hass, draft_id)


def _validate_name(value: Any) -> str:
    """Validate and normalize a source/entity name."""
    name = str(value).strip()
    if not name:
        raise ValueError("A name is required")
    return name


def _validate_stale_after(value: Any) -> int:
    """Validate a non-negative stale timeout."""
    try:
        stale_after = int(value)
    except (TypeError, ValueError) as err:
        raise ValueError("Stale timeout must be a whole number of seconds") from err
    if stale_after < 0:
        raise ValueError("Stale timeout cannot be negative")
    return stale_after


def _validate_fields(sample: JsonValue, raw_fields: Any) -> list[dict[str, Any]]:
    """Validate frontend mapping configuration against the sampled JSON shape."""
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("Select at least one value")

    fields: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            raise ValueError("Invalid field mapping")

        path = str(raw_field.get(FIELD_PATH, ""))
        if path in seen_paths:
            raise ValueError(
                f"The JSON path {pointer_to_label(path)} is selected more than once"
            )
        seen_paths.add(path)

        try:
            sample_value = get_by_pointer(sample, path)
        except (KeyError, TypeError) as err:
            raise ValueError(
                f"JSON path {pointer_to_label(path)} is not in the sample"
            ) from err

        name = _validate_name(raw_field.get(FIELD_NAME, ""))
        platform = str(raw_field.get(FIELD_PLATFORM, ""))
        if platform not in (PLATFORM_SENSOR, PLATFORM_BINARY_SENSOR):
            raise ValueError(f"Unsupported entity type for {pointer_to_label(path)}")

        field: dict[str, Any] = {
            FIELD_PATH: path,
            FIELD_NAME: name,
            FIELD_PLATFORM: platform,
        }

        if platform == PLATFORM_BINARY_SENSOR:
            if not isinstance(sample_value, bool):
                raise ValueError(
                    f"{pointer_to_label(path)} can only be a binary sensor when the sample is true or false"
                )
            fields.append(field)
            continue

        store_in_attribute = bool(raw_field.get(FIELD_STORE_IN_ATTRIBUTE, False))
        unit = str(raw_field.get(FIELD_UNIT, "")).strip()
        if _requires_attribute_storage(sample_value) and not store_in_attribute:
            raise ValueError(
                f"{pointer_to_label(path)} must use attribute storage because it cannot fit in a normal sensor state"
            )
        if store_in_attribute:
            if unit:
                raise ValueError(
                    "Attribute-backed sensors cannot have a unit of measurement"
                )
            field[FIELD_STORE_IN_ATTRIBUTE] = True
        elif unit:
            if not (
                isinstance(sample_value, (int, float))
                and not isinstance(sample_value, bool)
            ):
                raise ValueError(
                    "A unit of measurement requires a numeric sample value"
                )
            field[FIELD_UNIT] = unit

        fields.append(field)

    return fields


def _cleanup_removed_entities(
    hass: HomeAssistant,
    source_id: str,
    old_subentry: ConfigSubentry,
    new_fields: list[dict[str, Any]],
) -> None:
    """Remove registry entries for mappings deleted or moved to another platform."""
    registry = er.async_get(hass)
    new_by_path = {
        str(field[FIELD_PATH]): str(field[FIELD_PLATFORM]) for field in new_fields
    }

    for old_field in old_subentry.data.get(CONF_FIELDS, []):
        path = str(old_field[FIELD_PATH])
        old_platform = str(old_field[FIELD_PLATFORM])
        if new_by_path.get(path) == old_platform:
            continue

        unique_id = f"{source_id}:{path}"
        if entity_id := registry.async_get_entity_id(old_platform, DOMAIN, unique_id):
            registry.async_remove(entity_id)


async def _prepare_endpoint(
    hass: HomeAssistant,
    data: dict[str, Any],
    *,
    old_cloudhook_url: str | None = None,
) -> tuple[str, bool]:
    """Prepare source reachability and return URL plus cloudhook creation state."""
    webhook_id = str(data[CONF_WEBHOOK_ID])
    local_only = bool(data.get(CONF_LOCAL_ONLY, False))

    if local_only:
        data.pop(CONF_CLOUDHOOK_URL, None)
        if old_cloudhook_url and not await async_delete_cloudhook(hass, webhook_id):
            # Keep only as a cleanup marker. Local-only registration ignores it.
            data[CONF_CLOUDHOOK_URL] = old_cloudhook_url
        url, _cloudhook_url, _created = await async_resolve_webhook_url(
            hass, data, create_cloudhook=False
        )
        return url, False

    if old_cloudhook_url:
        data[CONF_CLOUDHOOK_URL] = old_cloudhook_url

    url, cloudhook_url, created = await async_resolve_webhook_url(hass, data)
    if cloudhook_url:
        data[CONF_CLOUDHOOK_URL] = cloudhook_url
    elif old_cloudhook_url:
        # Preserve the existing cloudhook identity during a temporary Cloud outage.
        data[CONF_CLOUDHOOK_URL] = old_cloudhook_url
    else:
        data.pop(CONF_CLOUDHOOK_URL, None)
    return url, created


async def _capture_handler(
    hass: HomeAssistant, draft_id: str, request: Request
) -> Response:
    """Capture the first valid JSON request into a frontend draft."""
    draft = _drafts(hass).get(draft_id)
    if draft is None:
        return json_response({"ok": False, "error": "capture_expired"}, status=410)

    try:
        sample = await async_read_json_payload(request)
    except PayloadValidationError as err:
        return payload_error_response(err)

    if not draft.sample_available:
        draft.sample = sample
        draft.sample_available = True
    return json_response(
        {"ok": True, "captured": True, "fields": len(_sample_nodes(draft.sample))}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/prepare_sample",
        vol.Required("sample_payload"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_prepare_sample(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse pasted JSON and create a short-lived mapping draft."""
    raw = str(msg["sample_payload"])
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        connection.send_error(
            msg["id"], "payload_too_large", "Payload exceeds 256 KiB"
        )
        return

    try:
        sample = parse_json(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError, RecursionError):
        connection.send_error(msg["id"], "invalid_json", "Enter valid JSON")
        return

    draft = _new_draft(hass, sample)
    connection.send_result(
        msg["id"],
        {"draft_id": draft.draft_id, "nodes": _sample_nodes(sample)},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/capture/start",
        vol.Required("local_only"): bool,
        vol.Optional("source_name", default="Push source"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_capture_start(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a temporary endpoint that captures one real JSON request."""
    draft = _new_draft(hass)
    capture_webhook_id = webhook.async_generate_id()
    draft.capture_webhook_id = capture_webhook_id
    local_only = bool(msg["local_only"])
    source_name = str(msg.get("source_name", "Push source")).strip() or "Push source"

    async def _handle(
        _hass: HomeAssistant, _webhook_id: str, request: Request
    ) -> Response:
        return await _capture_handler(hass, draft.draft_id, request)

    webhook.async_register(
        hass,
        DOMAIN,
        f"{source_name} panel setup capture",
        capture_webhook_id,
        _handle,
        local_only=local_only,
        allowed_methods={"POST"},
    )
    draft.capture_registered = True

    try:
        url, cloudhook_url, _created = await async_resolve_webhook_url(
            hass,
            {
                CONF_WEBHOOK_ID: capture_webhook_id,
                CONF_LOCAL_ONLY: local_only,
            },
        )
    except Exception:
        await async_delete_management_draft(hass, draft.draft_id)
        raise

    draft.capture_url = url
    draft.capture_cloudhook_url = cloudhook_url
    connection.send_result(
        msg["id"],
        {"draft_id": draft.draft_id, "capture_url": url},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/capture/status",
        vol.Required("draft_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_capture_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return whether a live-capture draft has received valid JSON."""
    draft = _drafts(hass).get(str(msg["draft_id"]))
    if draft is None:
        connection.send_error(msg["id"], "not_found", "Capture session expired")
        return

    if not draft.sample_available:
        connection.send_result(msg["id"], {"captured": False})
        return

    connection.send_result(
        msg["id"],
        {"captured": True, "nodes": _sample_nodes(draft.sample)},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/draft/cancel",
        vol.Required("draft_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_draft_cancel(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Cancel a pasted/live setup draft and clean its temporary endpoints."""
    await async_delete_management_draft(hass, str(msg["draft_id"]))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/save",
        vol.Optional("source_id"): str,
        vol.Required("name"): str,
        vol.Required("enabled"): bool,
        vol.Required("local_only"): bool,
        vol.Required("stale_after"): vol.Any(int, str),
        vol.Required("replace_mappings"): bool,
        vol.Optional("draft_id"): str,
        vol.Optional("fields", default=[]): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_source_save(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or edit a push source directly from the management panel."""
    entry = _parent_entry(hass)
    if entry is None:
        connection.send_error(
            msg["id"], "not_found", "HTTP Data Bridge is not configured"
        )
        return

    try:
        name = _validate_name(msg["name"])
        stale_after = _validate_stale_after(msg["stale_after"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_input", str(err))
        return

    source_id_arg = str(msg.get("source_id", "")).strip()
    old_subentry = _source_by_id(entry, source_id_arg) if source_id_arg else None
    if source_id_arg and old_subentry is None:
        connection.send_error(msg["id"], "not_found", "Push source not found")
        return

    replace_mappings = bool(msg["replace_mappings"])
    draft: FrontendDraft | None = None
    if old_subentry is None or replace_mappings:
        draft_id = str(msg.get("draft_id", ""))
        draft = _drafts(hass).get(draft_id)
        if draft is None or not draft.sample_available:
            connection.send_error(
                msg["id"],
                "sample_required",
                "Capture or paste an example payload before saving mappings",
            )
            return
        try:
            fields = _validate_fields(draft.sample, msg.get("fields", []))
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_mapping", str(err))
            return
    else:
        fields = [dict(field) for field in old_subentry.data.get(CONF_FIELDS, [])]

    enabled = bool(msg["enabled"])
    local_only = bool(msg["local_only"])
    created_cloudhook = False

    if old_subentry is None:
        source_id = uuid4().hex
        webhook_id = webhook.async_generate_id()
        data: dict[str, Any] = {
            CONF_SOURCE_NAME: name,
            CONF_SOURCE_ID: source_id,
            CONF_WEBHOOK_ID: webhook_id,
            CONF_STALE_AFTER: stale_after,
            CONF_LOCAL_ONLY: local_only,
            CONF_ENABLED: enabled,
            CONF_FIELDS: fields,
        }
        try:
            webhook_url, created_cloudhook = await _prepare_endpoint(hass, data)
            subentry = ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_TYPE_SOURCE,
                title=name,
                unique_id=None,
            )
            if not hass.config_entries.async_add_subentry(entry, subentry):
                raise ValueError("Home Assistant rejected the new source")
        except Exception as err:
            if created_cloudhook:
                await async_delete_cloudhook(hass, webhook_id)
            connection.send_error(msg["id"], "save_failed", str(err))
            return
    else:
        source_id = str(old_subentry.data[CONF_SOURCE_ID])
        webhook_id = str(old_subentry.data[CONF_WEBHOOK_ID])
        data = dict(old_subentry.data)
        data.update(
            {
                CONF_SOURCE_NAME: name,
                CONF_SOURCE_ID: source_id,
                CONF_WEBHOOK_ID: webhook_id,
                CONF_STALE_AFTER: stale_after,
                CONF_LOCAL_ONLY: local_only,
                CONF_ENABLED: enabled,
                CONF_FIELDS: fields,
            }
        )
        old_cloudhook = old_subentry.data.get(CONF_CLOUDHOOK_URL)
        try:
            webhook_url, created_cloudhook = await _prepare_endpoint(
                hass,
                data,
                old_cloudhook_url=str(old_cloudhook) if old_cloudhook else None,
            )
            hass.config_entries.async_update_subentry(
                entry,
                old_subentry,
                title=name,
                data=data,
            )
        except Exception as err:
            if created_cloudhook:
                await async_delete_cloudhook(hass, webhook_id)
            connection.send_error(msg["id"], "save_failed", str(err))
            return

        if replace_mappings:
            _cleanup_removed_entities(hass, source_id, old_subentry, fields)

    if draft is not None:
        await async_delete_management_draft(hass, draft.draft_id)

    connection.send_result(
        msg["id"],
        {
            "source_id": source_id,
            "webhook_url": webhook_url,
            "created": old_subentry is None,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/source/delete",
        vol.Required("source_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_source_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a push source through Home Assistant's canonical subentry API."""
    entry = _parent_entry(hass)
    if entry is None:
        connection.send_error(
            msg["id"], "not_found", "HTTP Data Bridge is not configured"
        )
        return

    subentry = _source_by_id(entry, str(msg["source_id"]))
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "Push source not found")
        return

    if not hass.config_entries.async_remove_subentry(entry, subentry.subentry_id):
        connection.send_error(
            msg["id"], "delete_failed", "Home Assistant rejected source deletion"
        )
        return

    connection.send_result(msg["id"])


def async_register_management_commands(hass: HomeAssistant) -> None:
    """Register all admin-only source management WebSocket commands."""
    websocket_api.async_register_command(hass, websocket_prepare_sample)
    websocket_api.async_register_command(hass, websocket_capture_start)
    websocket_api.async_register_command(hass, websocket_capture_status)
    websocket_api.async_register_command(hass, websocket_draft_cancel)
    websocket_api.async_register_command(hass, websocket_source_save)
    websocket_api.async_register_command(hass, websocket_source_delete)
