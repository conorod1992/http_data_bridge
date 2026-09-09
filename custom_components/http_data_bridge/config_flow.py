"""Config and source-subentry flows for HTTP Data Bridge."""

from __future__ import annotations

import json
from typing import Any, override
from uuid import uuid4

from aiohttp.web import Request, Response, json_response
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    FlowType,
    SubentryFlowContext,
    SubentryFlowResult,
)
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_ENABLED,
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SAMPLE_METHOD,
    CONF_SAMPLE_PAYLOAD,
    CONF_SOURCE_ID,
    CONF_SOURCE_NAME,
    CONF_STALE_AFTER,
    CONF_WEBHOOK_ID,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_STORE_IN_ATTRIBUTE,
    FIELD_UNIT,
    MAX_PAYLOAD_BYTES,
    PARENT_TITLE,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
    SAMPLE_METHOD_KEEP,
    SAMPLE_METHOD_LIVE,
    SAMPLE_METHOD_PASTE,
    SUBENTRY_TYPE_SOURCE,
)
from .helpers import (
    JsonValue,
    iter_json_nodes,
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

_JSON_TEXT_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True)
)
_TEXT_SELECTOR = selector.TextSelector(selector.TextSelectorConfig())
_STALE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="s",
    )
)
_NEW_SAMPLE_METHOD_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(
                value=SAMPLE_METHOD_LIVE,
                label="Capture a live request",
            ),
            selector.SelectOptionDict(
                value=SAMPLE_METHOD_PASTE,
                label="Paste example JSON",
            ),
        ]
    )
)
_RECONFIGURE_SAMPLE_METHOD_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(
                value=SAMPLE_METHOD_KEEP,
                label="Keep existing mappings",
            ),
            selector.SelectOptionDict(
                value=SAMPLE_METHOD_LIVE,
                label="Capture a live request and replace mappings",
            ),
            selector.SelectOptionDict(
                value=SAMPLE_METHOD_PASTE,
                label="Paste example JSON and replace mappings",
            ),
        ]
    )
)


def _parse_sample(raw: str) -> JsonValue:
    """Parse and validate a sample JSON payload."""
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise OverflowError
    return parse_json(raw)


def _selectable_fields(sample: JsonValue) -> dict[str, JsonValue]:
    """Return every selectable JSON node, including root and containers."""
    return dict(iter_json_nodes(sample))


def _requires_attribute_storage(value: JsonValue) -> bool:
    """Return whether the sample cannot be represented safely as sensor state."""
    if isinstance(value, (dict, list)):
        return True
    if isinstance(value, bool) or value is None:
        return False
    return len(str(value)) > MAX_LENGTH_STATE_STATE


def _storage_guidance(value: JsonValue) -> str:
    """Build novice-facing guidance for the sensor storage step."""
    if isinstance(value, dict):
        return (
            "This example is a JSON object, so attribute storage is required. "
            "The sensor state will be the time the object was received."
        )
    if isinstance(value, list):
        return (
            "This example is a JSON array, so attribute storage is required. "
            "The sensor state will be the time the array was received."
        )
    rendered_length = 0 if value is None else len(str(value))
    if rendered_length > MAX_LENGTH_STATE_STATE:
        return (
            f"This example is {rendered_length} characters long. Home Assistant "
            f"sensor states are limited to {MAX_LENGTH_STATE_STATE} characters, "
            "so attribute storage is required."
        )
    return (
        "Leave attribute storage off for a normal sensor state. Enable it when you "
        "want the complete value kept in the sensor's value attribute instead."
    )


async def _async_cleanup_previous_cloudhook(
    hass: HomeAssistant,
    data: dict[str, Any],
    webhook_id: str,
    cloudhook_url: str,
) -> None:
    """Delete an obsolete cloudhook or retain a durable retry marker."""
    if not await async_delete_cloudhook(hass, webhook_id):
        data[CONF_CLOUDHOOK_URL] = cloudhook_url


class HttpDataBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single HTTP Data Bridge parent entry."""

    VERSION = 2

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, _config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported source subentry types."""
        return {SUBENTRY_TYPE_SOURCE: HttpDataBridgeSourceFlow}

    @override
    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the one parent entry; source setup immediately follows."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title=PARENT_TITLE, data={})

    @override
    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Start the first push-source subentry flow after parent creation."""
        subentry_result = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, SUBENTRY_TYPE_SOURCE),
            context=SubentryFlowContext(source=SOURCE_USER),
        )
        result["next_flow"] = (
            FlowType.CONFIG_SUBENTRIES_FLOW,
            subentry_result["flow_id"],
        )
        return result


class HttpDataBridgeSourceFlow(ConfigSubentryFlow):
    """Create and reconfigure one HTTP push source."""

    def __init__(self) -> None:
        """Initialize flow state."""
        self._source_name = "Push source"
        self._source_id = ""
        self._stale_after = DEFAULT_STALE_AFTER
        self._local_only = False
        self._enabled = True
        self._webhook_id = ""
        self._cloudhook_url: str | None = None
        self._sample_payload: JsonValue = None
        self._sample_available = False
        self._sample_fields: dict[str, JsonValue] = {}
        self._selected_paths: list[str] = []
        self._field_configs: list[dict[str, Any]] = []
        self._field_index = 0
        self._pending_field: dict[str, Any] | None = None
        self._reconfiguring = False
        self._created_cloudhook = False
        self._committed = False

        # Live discovery deliberately uses a separate temporary webhook. This is
        # required for reconfiguration because the source's real webhook may
        # already be registered by its active runtime.
        self._capture_webhook_id = ""
        self._capture_registered = False
        self._capture_cloudhook_url: str | None = None
        self._capture_url = ""

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Start a new push source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            source_name = str(user_input[CONF_SOURCE_NAME]).strip()
            if not source_name:
                errors[CONF_SOURCE_NAME] = "empty_name"

            if not errors:
                self._source_name = source_name
                self._stale_after = int(user_input[CONF_STALE_AFTER])
                self._local_only = bool(user_input[CONF_LOCAL_ONLY])
                self._enabled = bool(user_input[CONF_ENABLED])
                self._ensure_source_identity()

                method = str(user_input[CONF_SAMPLE_METHOD])
                if method == SAMPLE_METHOD_LIVE:
                    return await self.async_step_capture()
                return await self.async_step_sample()

        return self.async_show_form(
            step_id="user",
            data_schema=self._source_schema(user_input or {}, reconfigure=False),
            errors=errors,
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing source."""
        subentry = self._get_reconfigure_subentry()
        self._reconfiguring = True
        errors: dict[str, str] = {}

        if not self._source_id:
            self._source_id = str(subentry.data[CONF_SOURCE_ID])
            self._webhook_id = str(subentry.data[CONF_WEBHOOK_ID])
            cloudhook = subentry.data.get(CONF_CLOUDHOOK_URL)
            self._cloudhook_url = str(cloudhook) if cloudhook else None

        if user_input is not None:
            source_name = str(user_input[CONF_SOURCE_NAME]).strip()
            if not source_name:
                errors[CONF_SOURCE_NAME] = "empty_name"

            if not errors:
                self._source_name = source_name
                self._stale_after = int(user_input[CONF_STALE_AFTER])
                self._local_only = bool(user_input[CONF_LOCAL_ONLY])
                self._enabled = bool(user_input[CONF_ENABLED])

                method = str(user_input[CONF_SAMPLE_METHOD])
                if method == SAMPLE_METHOD_KEEP:
                    self._field_configs = list(subentry.data.get(CONF_FIELDS, []))
                    return await self.async_step_confirm_existing()
                if method == SAMPLE_METHOD_LIVE:
                    return await self.async_step_capture()
                return await self.async_step_sample()

        defaults = dict(subentry.data)
        defaults[CONF_SAMPLE_METHOD] = SAMPLE_METHOD_KEEP
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._source_schema(
                defaults if user_input is None else user_input,
                reconfigure=True,
            ),
            errors=errors,
        )

    def _source_schema(
        self, values: dict[str, Any], *, reconfigure: bool
    ) -> vol.Schema:
        """Build source settings schema."""
        method_default = SAMPLE_METHOD_KEEP if reconfigure else SAMPLE_METHOD_LIVE
        method_selector = (
            _RECONFIGURE_SAMPLE_METHOD_SELECTOR
            if reconfigure
            else _NEW_SAMPLE_METHOD_SELECTOR
        )

        return vol.Schema(
            {
                vol.Required(
                    CONF_SOURCE_NAME,
                    default=values.get(CONF_SOURCE_NAME, "Push source"),
                ): _TEXT_SELECTOR,
                vol.Required(
                    CONF_SAMPLE_METHOD,
                    default=values.get(CONF_SAMPLE_METHOD, method_default),
                ): method_selector,
                vol.Required(
                    CONF_STALE_AFTER,
                    default=values.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER),
                ): _STALE_SELECTOR,
                vol.Required(
                    CONF_LOCAL_ONLY,
                    default=values.get(CONF_LOCAL_ONLY, False),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_ENABLED,
                    default=values.get(CONF_ENABLED, True),
                ): selector.BooleanSelector(),
            }
        )

    async def async_step_sample(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Accept a pasted example JSON payload."""
        errors: dict[str, str] = {}
        raw_sample = (
            str(user_input.get(CONF_SAMPLE_PAYLOAD, "")).strip()
            if user_input is not None
            else ""
        )

        if user_input is not None:
            try:
                sample = _parse_sample(raw_sample)
            except OverflowError:
                errors[CONF_SAMPLE_PAYLOAD] = "payload_too_large"
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError, RecursionError):
                errors[CONF_SAMPLE_PAYLOAD] = "invalid_json"
            else:
                self._set_sample(sample, _selectable_fields(sample))
                return await self.async_step_select_fields()

        return self.async_show_form(
            step_id="sample",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SAMPLE_PAYLOAD,
                        default=(
                            raw_sample
                            if raw_sample
                            else '{\n  "temperature": 21.4,\n  "online": true\n}'
                        ),
                    ): _JSON_TEXT_SELECTOR
                }
            ),
            errors=errors,
        )

    async def async_step_capture(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Capture one real request through a temporary webhook."""
        capture_url = await self._ensure_capture_endpoint()

        if user_input is not None and self._sample_available:
            await self._async_cleanup_capture()
            return await self.async_step_select_fields()

        errors = {"base": "no_payload_received"} if user_input is not None else {}
        return self.async_show_form(
            step_id="capture",
            data_schema=vol.Schema({}),
            description_placeholders={"capture_url": capture_url},
            errors=errors,
        )

    async def async_step_select_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose JSON values that should become Home Assistant entities."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = list(user_input.get(CONF_FIELDS, []))
            if not selected:
                errors[CONF_FIELDS] = "select_at_least_one"
            else:
                self._selected_paths = selected
                self._field_configs = []
                self._field_index = 0
                self._pending_field = None
                return await self.async_step_configure_field()

        options = [
            selector.SelectOptionDict(
                value=path,
                label=f"{pointer_to_label(path)} — {sample_display(value)}",
            )
            for path, value in self._sample_fields.items()
        ]

        return self.async_show_form(
            step_id="select_fields",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FIELDS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_configure_field(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure name and entity type for one selected JSON value."""
        path = self._selected_paths[self._field_index]
        sample = self._sample_fields[path]
        is_boolean = isinstance(sample, bool)
        default_platform = PLATFORM_BINARY_SENSOR if is_boolean else PLATFORM_SENSOR
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_name = str(user_input[FIELD_NAME]).strip()
            platform = str(user_input[FIELD_PLATFORM])

            if not entity_name:
                errors[FIELD_NAME] = "empty_name"
            if platform == PLATFORM_BINARY_SENSOR and not is_boolean:
                errors[FIELD_PLATFORM] = "binary_requires_boolean"

            if not errors:
                field: dict[str, Any] = {
                    FIELD_PATH: path,
                    FIELD_NAME: entity_name,
                    FIELD_PLATFORM: platform,
                }
                if platform == PLATFORM_SENSOR:
                    self._pending_field = field
                    return await self.async_step_configure_sensor()

                self._field_configs.append(field)
                return await self._advance_field()

        platform_options = [
            selector.SelectOptionDict(value=PLATFORM_SENSOR, label="Sensor")
        ]
        if is_boolean:
            platform_options.insert(
                0,
                selector.SelectOptionDict(
                    value=PLATFORM_BINARY_SENSOR,
                    label="Binary sensor",
                ),
            )

        schema: dict[vol.Marker, Any] = {
            vol.Required(
                FIELD_NAME,
                default=(
                    user_input.get(FIELD_NAME, suggested_name(path))
                    if user_input
                    else suggested_name(path)
                ),
            ): _TEXT_SELECTOR,
            vol.Required(
                FIELD_PLATFORM,
                default=(
                    user_input.get(FIELD_PLATFORM, default_platform)
                    if user_input
                    else default_platform
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=platform_options)
            ),
        }

        type_guidance = (
            "Binary sensor is recommended for JSON true/false and represents an "
            "on/off state. Sensor stores the value as ordinary sensor data."
            if is_boolean
            else "This JSON value can be exposed as a Home Assistant sensor."
        )

        return self.async_show_form(
            step_id="configure_field",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "field_path": pointer_to_label(path),
                "sample_value": sample_display(sample),
                "current": str(self._field_index + 1),
                "total": str(len(self._selected_paths)),
                "type_guidance": type_guidance,
            },
            errors=errors,
        )

    async def async_step_configure_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure normal-state versus attribute storage for a sensor."""
        assert self._pending_field is not None
        path = str(self._pending_field[FIELD_PATH])
        sample = self._sample_fields[path]
        is_number = isinstance(sample, (int, float)) and not isinstance(sample, bool)
        requires_attribute = _requires_attribute_storage(sample)
        errors: dict[str, str] = {}

        if user_input is not None:
            store_in_attribute = bool(user_input[FIELD_STORE_IN_ATTRIBUTE])
            unit = str(user_input.get(FIELD_UNIT, "")).strip()

            if requires_attribute and not store_in_attribute:
                errors[FIELD_STORE_IN_ATTRIBUTE] = "attribute_required"
            if store_in_attribute and unit:
                errors[FIELD_UNIT] = "attribute_cannot_have_unit"

            if not errors:
                field = dict(self._pending_field)
                if store_in_attribute:
                    field[FIELD_STORE_IN_ATTRIBUTE] = True
                elif unit:
                    field[FIELD_UNIT] = unit
                self._field_configs.append(field)
                self._pending_field = None
                return await self._advance_field()

        schema: dict[vol.Marker, Any] = {
            vol.Required(
                FIELD_STORE_IN_ATTRIBUTE,
                default=(
                    bool(user_input[FIELD_STORE_IN_ATTRIBUTE])
                    if user_input is not None
                    else requires_attribute
                ),
            ): selector.BooleanSelector(),
        }
        if is_number:
            schema[
                vol.Optional(
                    FIELD_UNIT,
                    default=user_input.get(FIELD_UNIT, "") if user_input else "",
                )
            ] = _TEXT_SELECTOR

        return self.async_show_form(
            step_id="configure_sensor",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "field_path": pointer_to_label(path),
                "storage_guidance": _storage_guidance(sample),
                "state_limit": str(MAX_LENGTH_STATE_STATE),
            },
            errors=errors,
        )

    async def _advance_field(self) -> SubentryFlowResult:
        """Advance to the next selected value or final confirmation."""
        self._field_index += 1
        if self._field_index < len(self._selected_paths):
            return await self.async_step_configure_field()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show sender examples before saving a mapped source."""
        if user_input is not None:
            return await self._finish_flow()

        assert self._sample_available
        sample = self._sample_payload
        url = await self._webhook_url()
        payload = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "webhook_url": url,
                "curl_example": self._curl_example(url, payload),
                "javascript_example": self._javascript_example(url, sample),
                "php_example": self._php_example(url, payload),
            },
        )

    async def async_step_confirm_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm settings-only source reconfiguration."""
        if user_input is not None:
            return await self._finish_flow()

        return self.async_show_form(
            step_id="confirm_existing",
            description_placeholders={"webhook_url": await self._webhook_url()},
        )

    def _ensure_source_identity(self) -> None:
        """Allocate stable source identity once for a new source flow."""
        if not self._source_id:
            self._source_id = uuid4().hex
        if not self._webhook_id:
            self._webhook_id = webhook.async_generate_id()

    def _set_sample(
        self, sample: JsonValue, sample_fields: dict[str, JsonValue]
    ) -> None:
        """Store one setup-only sample in flow memory."""
        self._sample_payload = sample
        self._sample_fields = sample_fields
        self._sample_available = True

    async def _ensure_capture_endpoint(self) -> str:
        """Register and resolve the temporary live-capture endpoint."""
        if self._capture_registered:
            return self._capture_url

        self._capture_webhook_id = webhook.async_generate_id()
        webhook.async_register(
            self.hass,
            DOMAIN,
            f"{self._source_name} setup capture",
            self._capture_webhook_id,
            self._async_handle_capture,
            local_only=self._local_only,
            allowed_methods={"POST"},
        )
        self._capture_registered = True

        try:
            url, cloudhook_url, _created = await async_resolve_webhook_url(
                self.hass,
                {
                    CONF_WEBHOOK_ID: self._capture_webhook_id,
                    CONF_LOCAL_ONLY: self._local_only,
                },
            )
        except Exception:
            webhook.async_unregister(self.hass, self._capture_webhook_id)
            self._capture_registered = False
            self._capture_webhook_id = ""
            raise

        self._capture_url = url
        self._capture_cloudhook_url = cloudhook_url
        return url

    async def _async_handle_capture(
        self, _hass: HomeAssistant, _webhook_id: str, request: Request
    ) -> Response:
        """Capture the latest valid JSON request without persisting it."""
        try:
            sample = await async_read_json_payload(request)
        except PayloadValidationError as err:
            return payload_error_response(err)

        sample_fields = _selectable_fields(sample)
        self._set_sample(sample, sample_fields)
        return json_response(
            {"ok": True, "captured": True, "fields": len(sample_fields)}
        )

    async def _async_cleanup_capture(self) -> None:
        """Remove a temporary capture webhook/cloudhook."""
        capture_webhook_id = self._capture_webhook_id
        if self._capture_registered and capture_webhook_id:
            webhook.async_unregister(self.hass, capture_webhook_id)
            self._capture_registered = False

        if self._capture_cloudhook_url and capture_webhook_id:
            await async_delete_cloudhook(self.hass, capture_webhook_id)

        self._capture_webhook_id = ""
        self._capture_cloudhook_url = None
        self._capture_url = ""

    async def _finish_flow(self) -> SubentryFlowResult:
        """Create or update the source subentry."""
        await self._async_cleanup_capture()

        data: dict[str, Any] = {
            CONF_SOURCE_NAME: self._source_name,
            CONF_SOURCE_ID: self._source_id,
            CONF_WEBHOOK_ID: self._webhook_id,
            CONF_STALE_AFTER: self._stale_after,
            CONF_LOCAL_ONLY: self._local_only,
            CONF_ENABLED: self._enabled,
            CONF_FIELDS: self._field_configs,
        }
        if self._cloudhook_url and not self._local_only:
            data[CONF_CLOUDHOOK_URL] = self._cloudhook_url

        if self._reconfiguring:
            entry = self._get_entry()
            old_subentry = self._get_reconfigure_subentry()
            self._cleanup_removed_entities(old_subentry, self._field_configs)

            if self._local_only and (
                old_cloudhook := old_subentry.data.get(CONF_CLOUDHOOK_URL)
            ):
                # Stop relying on the cloudhook immediately. If Home Assistant
                # Cloud is temporarily unavailable, retain the URL only as a
                # durable cleanup marker so manager setup can retry later.
                await _async_cleanup_previous_cloudhook(
                    self.hass,
                    data,
                    self._webhook_id,
                    str(old_cloudhook),
                )

            self._committed = True
            return self.async_update_and_abort(
                entry,
                old_subentry,
                title=self._source_name,
                data=data,
            )

        self._committed = True
        return self.async_create_entry(title=self._source_name, data=data)

    def _cleanup_removed_entities(
        self,
        old_subentry,
        new_fields: list[dict[str, Any]],
    ) -> None:
        """Remove registry entries for mappings deleted or moved to another platform."""
        registry = er.async_get(self.hass)
        new_by_path = {
            str(field[FIELD_PATH]): str(field[FIELD_PLATFORM]) for field in new_fields
        }

        for old_field in old_subentry.data.get(CONF_FIELDS, []):
            path = str(old_field[FIELD_PATH])
            old_platform = str(old_field[FIELD_PLATFORM])
            if new_by_path.get(path) == old_platform:
                continue

            unique_id = f"{self._source_id}:{path}"
            if entity_id := registry.async_get_entity_id(
                old_platform,
                DOMAIN,
                unique_id,
            ):
                registry.async_remove(entity_id)

    async def _webhook_url(self) -> str:
        """Resolve a useful local/external/cloudhook URL for the pending source."""
        pending: dict[str, Any] = {
            CONF_WEBHOOK_ID: self._webhook_id,
            CONF_LOCAL_ONLY: self._local_only,
        }
        if self._cloudhook_url:
            pending[CONF_CLOUDHOOK_URL] = self._cloudhook_url

        url, cloudhook_url, created = await async_resolve_webhook_url(
            self.hass, pending
        )
        if self._local_only:
            self._cloudhook_url = None
        elif cloudhook_url:
            self._cloudhook_url = cloudhook_url
            # Only a brand-new source owns cleanup of a cloudhook created during
            # a flow. A reconfigure must not delete an existing live endpoint if
            # the user backs out.
            if created and not self._reconfiguring:
                self._created_cloudhook = True
        return url

    @callback
    @override
    def async_remove(self) -> None:
        """Clean up temporary endpoints created by an abandoned flow."""
        capture_webhook_id = self._capture_webhook_id
        if self._capture_registered and capture_webhook_id:
            webhook.async_unregister(self.hass, capture_webhook_id)
            self._capture_registered = False

        if self._capture_cloudhook_url and capture_webhook_id:
            self.hass.async_create_task(
                async_delete_cloudhook(self.hass, capture_webhook_id),
                "clean abandoned HTTP Data Bridge capture cloudhook",
            )

        if (
            self._created_cloudhook
            and not self._committed
            and self._webhook_id
        ):
            self.hass.async_create_task(
                async_delete_cloudhook(self.hass, self._webhook_id),
                "clean abandoned HTTP Data Bridge cloudhook",
            )

    @staticmethod
    def _curl_example(url: str, payload: str) -> str:
        """Build a cURL example."""
        escaped = payload.replace("'", "'\\''")
        return (
            "curl -X POST -H 'Content-Type: application/json' "
            f"--data '{escaped}' '{url}'"
        )

    @staticmethod
    def _javascript_example(url: str, sample: JsonValue) -> str:
        """Build a JavaScript fetch example."""
        payload = json.dumps(sample, ensure_ascii=False, indent=2)
        return (
            f"const payload = {payload};\n\n"
            f'await fetch("{url}", {{\n'
            '  method: "POST",\n'
            '  headers: {"Content-Type": "application/json"},\n'
            '  body: JSON.stringify(payload)\n'
            '});'
        )

    @staticmethod
    def _php_example(url: str, payload: str) -> str:
        """Build a PHP cURL example."""
        escaped_url = url.replace("'", "\\'")
        escaped_payload = payload.replace("\\", "\\\\").replace("'", "\\'")
        return (
            "$ch = curl_init('" + escaped_url + "');\n"
            "curl_setopt_array($ch, [\n"
            "    CURLOPT_POST => true,\n"
            "    CURLOPT_HTTPHEADER => ['Content-Type: application/json'],\n"
            "    CURLOPT_POSTFIELDS => '" + escaped_payload + "',\n"
            "    CURLOPT_RETURNTRANSFER => true,\n"
            "]);\n"
            "curl_exec($ch);\n"
            "curl_close($ch);"
        )