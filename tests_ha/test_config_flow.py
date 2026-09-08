"""Home Assistant config-flow tests for HTTP Data Bridge."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.http_data_bridge.const import (
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
    CONF_SAMPLE_PAYLOAD,
    CONF_SOURCE_NAME,
    CONF_STALE_AFTER,
    CONF_WEBHOOK_ID,
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_UNIT,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
)


async def test_guided_setup_creates_sensor_and_binary_sensor_mappings(
    hass: HomeAssistant,
) -> None:
    """A sample payload should drive the complete guided setup."""
    with (
        patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
            return_value="generated-webhook-id",
        ),
        patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_url",
            return_value="https://ha.example/api/webhook/generated-webhook-id",
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Website status",
                CONF_SAMPLE_PAYLOAD: '{"temperature":21.4,"online":true}',
                CONF_STALE_AFTER: 120,
                CONF_LOCAL_ONLY: False,
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_fields"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_FIELDS: ["/temperature", "/online"]},
        )
        assert result["step_id"] == "configure_field"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                FIELD_NAME: "Temperature",
                FIELD_PLATFORM: PLATFORM_SENSOR,
                FIELD_UNIT: "°C",
            },
        )
        assert result["step_id"] == "configure_field"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                FIELD_NAME: "Online",
                FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "confirm"
        assert "generated-webhook-id" in result["description_placeholders"]["webhook_url"]
        assert 'fetch("https://ha.example/api/webhook/generated-webhook-id"' in result[
            "description_placeholders"
        ]["javascript_example"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Website status"
    assert result["data"][CONF_WEBHOOK_ID] == "generated-webhook-id"
    assert result["data"][CONF_STALE_AFTER] == 120
    assert result["data"][CONF_FIELDS] == [
        {
            FIELD_PATH: "/temperature",
            FIELD_NAME: "Temperature",
            FIELD_PLATFORM: PLATFORM_SENSOR,
            FIELD_UNIT: "°C",
        },
        {
            FIELD_PATH: "/online",
            FIELD_NAME: "Online",
            FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
        },
    ]


async def test_invalid_sample_and_empty_name_are_recoverable(
    hass: HomeAssistant,
) -> None:
    """Bad sample JSON and a blank source name should remain on the first form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_SOURCE_NAME: "   ",
            CONF_SAMPLE_PAYLOAD: "{not-json}",
            CONF_STALE_AFTER: 0,
            CONF_LOCAL_ONLY: False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"][CONF_SOURCE_NAME] == "empty_name"
    assert result["errors"][CONF_SAMPLE_PAYLOAD] == "invalid_json"


async def test_non_finite_and_overly_deep_samples_are_rejected(
    hass: HomeAssistant,
) -> None:
    """Samples the runtime cannot represent safely should be rejected during setup."""
    samples = (
        '{"value":1e400}',
        "[" * 2000 + "0" + "]" * 2000,
    )

    for sample in samples:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Invalid source",
                CONF_SAMPLE_PAYLOAD: sample,
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: False,
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"][CONF_SAMPLE_PAYLOAD] == "invalid_json"


async def test_null_root_sample_can_be_mapped(hass: HomeAssistant) -> None:
    """JSON null is a valid scalar sample and should not be mistaken for no sample."""
    with patch(
        "custom_components.http_data_bridge.config_flow.webhook.async_generate_url",
        return_value="https://ha.example/api/webhook/null-test",
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Root value",
                CONF_SAMPLE_PAYLOAD: "null",
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: True,
            },
        )
        assert result["step_id"] == "select_fields"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_FIELDS: [""]}
        )
        assert result["step_id"] == "configure_field"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={FIELD_NAME: "Value", FIELD_PLATFORM: PLATFORM_SENSOR},
        )
        assert result["step_id"] == "confirm"


async def test_settings_only_reconfigure_preserves_webhook_and_mappings(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """A settings-only reconfigure should not require or replace sample mappings."""
    entry = entry_factory()
    original_fields = list(entry.data[CONF_FIELDS])

    with patch(
        "custom_components.http_data_bridge.config_flow.webhook.async_generate_url",
        return_value="https://ha.example/api/webhook/test-http-data-bridge-webhook",
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Renamed source",
                CONF_SAMPLE_PAYLOAD: "",
                CONF_STALE_AFTER: 300,
                CONF_LOCAL_ONLY: True,
            },
        )
        assert result["step_id"] == "confirm_existing"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Renamed source"
    assert entry.data[CONF_WEBHOOK_ID] == "test-http-data-bridge-webhook"
    assert entry.data[CONF_FIELDS] == original_fields
    assert entry.data[CONF_STALE_AFTER] == 300
    assert entry.data[CONF_LOCAL_ONLY] is True
