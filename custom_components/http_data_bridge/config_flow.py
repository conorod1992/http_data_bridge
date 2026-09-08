"""Config flow for HTTP Data Bridge."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.network import NoURLAvailableError

from .const import (
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SAMPLE_PAYLOAD,
    CONF_SOURCE_NAME,
    CONF_STALE_AFTER,
    CONF_WEBHOOK_ID,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_UNIT,
    MAX_PAYLOAD_BYTES,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
)
from .helpers import (
    JsonValue,
    iter_scalar_fields,
    parse_json,
    pointer_to_label,
    sample_display,
    suggested_name,
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


def _parse_sample(raw: str) -> JsonValue:
    """Parse and validate a sample JSON payload."""
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise OverflowError
    return parse_json(raw)


class HttpDataBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle HTTP Data Bridge configuration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._source_name = "Push source"
        self._stale_after = DEFAULT_STALE_AFTER
        self._local_only = False
        self._webhook_id = ""
        self._sample_payload: JsonValue | None = None
        self._sample_available = False
        self._sample_fields: dict[str, JsonValue] = {}
        self._selected_paths: list[str] = []
        self._field_configs: list[dict[str, Any]] = []
        self._field_index = 0
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a new push source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            source_name = str(user_input[CONF_SOURCE_NAME]).strip()
            raw_sample = str(user_input[CONF_SAMPLE_PAYLOAD]).strip()

            if not source_name:
                errors[CONF_SOURCE_NAME] = "empty_name"

            try:
                sample = _parse_sample(raw_sample)
            except OverflowError:
                errors[CONF_SAMPLE_PAYLOAD] = "payload_too_large"
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError, RecursionError):
                errors[CONF_SAMPLE_PAYLOAD] = "invalid_json"
            else:
                sample_fields = dict(iter_scalar_fields(sample))
                if not sample_fields:
                    errors[CONF_SAMPLE_PAYLOAD] = "no_scalar_fields"

            if not errors:
                self._source_name = source_name
                self._stale_after = int(user_input[CONF_STALE_AFTER])
                self._local_only = bool(user_input[CONF_LOCAL_ONLY])
                self._webhook_id = webhook.async_generate_id()
                self._sample_payload = sample
                self._sample_available = True
                self._sample_fields = sample_fields
                return await self.async_step_select_fields()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SOURCE_NAME,
                        default=(
                            user_input.get(CONF_SOURCE_NAME, "Push source")
                            if user_input
                            else "Push source"
                        ),
                    ): _TEXT_SELECTOR,
                    vol.Required(
                        CONF_SAMPLE_PAYLOAD,
                        default=(
                            user_input.get(
                                CONF_SAMPLE_PAYLOAD,
                                '{\n  "temperature": 21.4,\n  "online": true\n}',
                            )
                            if user_input
                            else '{\n  "temperature": 21.4,\n  "online": true\n}'
                        ),
                    ): _JSON_TEXT_SELECTOR,
                    vol.Required(
                        CONF_STALE_AFTER,
                        default=(
                            user_input.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)
                            if user_input
                            else DEFAULT_STALE_AFTER
                        ),
                    ): _STALE_SELECTOR,
                    vol.Required(
                        CONF_LOCAL_ONLY,
                        default=(
                            user_input.get(CONF_LOCAL_ONLY, False)
                            if user_input
                            else False
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an existing push source."""
        entry = self._get_reconfigure_entry()
        self._reconfigure_entry = entry
        errors: dict[str, str] = {}

        if user_input is not None:
            source_name = str(user_input[CONF_SOURCE_NAME]).strip()
            raw_sample = str(user_input.get(CONF_SAMPLE_PAYLOAD, "")).strip()

            if not source_name:
                errors[CONF_SOURCE_NAME] = "empty_name"

            self._source_name = source_name
            self._stale_after = int(user_input[CONF_STALE_AFTER])
            self._local_only = bool(user_input[CONF_LOCAL_ONLY])
            self._webhook_id = str(entry.data[CONF_WEBHOOK_ID])

            if not errors and not raw_sample:
                self._field_configs = list(entry.data.get(CONF_FIELDS, []))
                return await self.async_step_confirm_existing()

            if raw_sample:
                try:
                    sample = _parse_sample(raw_sample)
                except OverflowError:
                    errors[CONF_SAMPLE_PAYLOAD] = "payload_too_large"
                except (
                    json.JSONDecodeError,
                    ValueError,
                    UnicodeDecodeError,
                    RecursionError,
                ):
                    errors[CONF_SAMPLE_PAYLOAD] = "invalid_json"
                else:
                    sample_fields = dict(iter_scalar_fields(sample))
                    if not sample_fields:
                        errors[CONF_SAMPLE_PAYLOAD] = "no_scalar_fields"
                    elif not errors:
                        self._sample_payload = sample
                        self._sample_available = True
                        self._sample_fields = sample_fields
                        return await self.async_step_select_fields()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SOURCE_NAME,
                        default=entry.data.get(CONF_SOURCE_NAME, entry.title),
                    ): _TEXT_SELECTOR,
                    vol.Optional(CONF_SAMPLE_PAYLOAD, default=""): _JSON_TEXT_SELECTOR,
                    vol.Required(
                        CONF_STALE_AFTER,
                        default=entry.data.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER),
                    ): _STALE_SELECTOR,
                    vol.Required(
                        CONF_LOCAL_ONLY,
                        default=entry.data.get(CONF_LOCAL_ONLY, False),
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_select_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which scalar values should become Home Assistant entities."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = list(user_input.get(CONF_FIELDS, []))
            if not selected:
                errors[CONF_FIELDS] = "select_at_least_one"
            else:
                self._selected_paths = selected
                self._field_configs = []
                self._field_index = 0
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
    ) -> ConfigFlowResult:
        """Configure one selected field at a time."""
        path = self._selected_paths[self._field_index]
        sample = self._sample_fields[path]
        is_boolean = isinstance(sample, bool)
        is_number = isinstance(sample, (int, float)) and not is_boolean
        default_platform = PLATFORM_BINARY_SENSOR if is_boolean else PLATFORM_SENSOR
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_name = str(user_input[FIELD_NAME]).strip()
            platform = str(user_input[FIELD_PLATFORM])

            if not entity_name:
                errors[FIELD_NAME] = "empty_name"
            if platform == PLATFORM_BINARY_SENSOR and not is_boolean:
                errors[FIELD_PLATFORM] = "binary_requires_boolean"
            unit = str(user_input.get(FIELD_UNIT, "")).strip()
            if unit and not is_number:
                errors[FIELD_UNIT] = "unit_requires_number"

            if not errors:
                field: dict[str, Any] = {
                    FIELD_PATH: path,
                    FIELD_NAME: entity_name,
                    FIELD_PLATFORM: platform,
                }
                if platform == PLATFORM_SENSOR and unit:
                    field[FIELD_UNIT] = unit
                self._field_configs.append(field)
                self._field_index += 1

                if self._field_index < len(self._selected_paths):
                    return await self.async_step_configure_field()
                return await self.async_step_confirm()

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
        if is_number:
            schema[
                vol.Optional(
                    FIELD_UNIT,
                    default=(
                        user_input.get(FIELD_UNIT, "") if user_input else ""
                    ),
                )
            ] = _TEXT_SELECTOR

        return self.async_show_form(
            step_id="configure_field",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "field_path": pointer_to_label(path),
                "sample_value": sample_display(sample),
                "current": str(self._field_index + 1),
                "total": str(len(self._selected_paths)),
            },
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show sender examples before saving a newly mapped source."""
        if user_input is not None:
            return self._finish_flow()

        assert self._sample_available
        sample = self._sample_payload
        url = self._webhook_url()
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
    ) -> ConfigFlowResult:
        """Confirm settings-only reconfiguration without inventing payload examples."""
        if user_input is not None:
            return self._finish_flow()

        return self.async_show_form(
            step_id="confirm_existing",
            description_placeholders={"webhook_url": self._webhook_url()},
        )

    def _finish_flow(self) -> ConfigFlowResult:
        """Create or update the config entry."""
        data = {
            CONF_SOURCE_NAME: self._source_name,
            CONF_WEBHOOK_ID: self._webhook_id,
            CONF_STALE_AFTER: self._stale_after,
            CONF_LOCAL_ONLY: self._local_only,
            CONF_FIELDS: self._field_configs,
        }

        if self._reconfigure_entry is not None:
            self._cleanup_removed_entities(self._reconfigure_entry, self._field_configs)
            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                title=self._source_name,
                data=data,
            )

        return self.async_create_entry(title=self._source_name, data=data)

    def _cleanup_removed_entities(
        self,
        entry: ConfigEntry,
        new_fields: list[dict[str, Any]],
    ) -> None:
        """Remove registry entries for mappings deleted or moved to another platform."""
        registry = er.async_get(self.hass)
        new_by_path = {
            str(field[FIELD_PATH]): str(field[FIELD_PLATFORM]) for field in new_fields
        }

        for old_field in entry.data.get(CONF_FIELDS, []):
            path = str(old_field[FIELD_PATH])
            old_platform = str(old_field[FIELD_PLATFORM])
            if new_by_path.get(path) == old_platform:
                continue

            unique_id = f"{entry.entry_id}:{path}"
            if entity_id := registry.async_get_entity_id(
                old_platform,
                DOMAIN,
                unique_id,
            ):
                registry.async_remove(entity_id)

    def _webhook_url(self) -> str:
        """Return a usable full webhook URL when HA can determine one."""
        try:
            return webhook.async_generate_url(self.hass, self._webhook_id)
        except NoURLAvailableError:
            return webhook.async_generate_path(self._webhook_id)

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
