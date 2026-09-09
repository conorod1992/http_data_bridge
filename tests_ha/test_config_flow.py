"""Home Assistant config/subentry-flow tests for HTTP Data Bridge."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.http_data_bridge.const import (
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
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    FIELD_UNIT,
    PARENT_TITLE,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
    SAMPLE_METHOD_KEEP,
    SAMPLE_METHOD_LIVE,
    SAMPLE_METHOD_PASTE,
    SUBENTRY_TYPE_SOURCE,
)


async def _start_source_flow(hass: HomeAssistant, entry) -> dict:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SOURCE),
        context={"source": config_entries.SOURCE_USER},
    )


def _fake_cloud(*, cloudhook_url: str | None = None) -> Mock:
    """Return the small Home Assistant Cloud surface source setup needs."""
    cloud = Mock()
    cloud.CloudNotAvailable = RuntimeError
    cloud.async_active_subscription.return_value = True
    cloud.async_is_connected.return_value = True
    cloud.async_get_or_create_cloudhook = AsyncMock(return_value=cloudhook_url)
    cloud.async_delete_cloudhook = AsyncMock()
    return cloud


async def _enter_pasted_sample(
    hass: HomeAssistant,
    parent,
    *,
    name: str = "Website status",
    payload: str = '{"temperature":21.4,"online":true}',
    stale_after: int = 120,
    local_only: bool = False,
) -> dict:
    """Enter source settings and one pasted setup sample."""
    result = await _start_source_flow(hass, parent)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_SOURCE_NAME: name,
            CONF_SAMPLE_METHOD: SAMPLE_METHOD_PASTE,
            CONF_STALE_AFTER: stale_after,
            CONF_LOCAL_ONLY: local_only,
            CONF_ENABLED: True,
        },
    )
    assert result["step_id"] == "sample"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={CONF_SAMPLE_PAYLOAD: payload},
    )


async def test_parent_flow_creates_single_parent_and_chains_source_flow(
    hass: HomeAssistant,
) -> None:
    """Adding the integration should create one parent then start Add source."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == PARENT_TITLE
    assert result["data"] == {}
    assert result["next_flow"][0] is config_entries.FlowType.CONFIG_SUBENTRIES_FLOW

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_pasted_sample_creates_native_mapping_subentry(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """The explicit pasted-sample path should still drive complete setup."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")

    with (
        patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
            return_value="generated-webhook-id",
        ),
        patch(
            "custom_components.http_data_bridge.config_flow.async_resolve_webhook_url",
            new_callable=AsyncMock,
            return_value=(
                "https://ha.example/api/webhook/generated-webhook-id",
                None,
                False,
            ),
        ),
    ):
        result = await _enter_pasted_sample(hass, parent)
        assert result["step_id"] == "select_fields"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={CONF_FIELDS: ["/temperature", "/online"]},
        )
        assert result["step_id"] == "configure_field"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                FIELD_NAME: "Temperature",
                FIELD_PLATFORM: PLATFORM_SENSOR,
                FIELD_UNIT: "°C",
            },
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                FIELD_NAME: "Online",
                FIELD_PLATFORM: PLATFORM_BINARY_SENSOR,
            },
        )
        assert result["step_id"] == "confirm"
        assert "generated-webhook-id" in result["description_placeholders"]["webhook_url"]

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    source = next(iter(parent.subentries.values()))
    assert source.title == "Website status"
    assert source.data[CONF_WEBHOOK_ID] == "generated-webhook-id"
    assert source.data[CONF_SOURCE_ID]
    assert source.data[CONF_STALE_AFTER] == 120
    assert source.data[CONF_FIELDS] == [
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


async def test_live_capture_uses_temporary_webhook_then_same_mapping_flow(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """A real request should be capturable without becoming persisted source data."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")

    with patch(
        "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
        side_effect=["final-webhook-id", "temporary-capture-id"],
    ):
        result = await _start_source_flow(hass, parent)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Live source",
                CONF_SAMPLE_METHOD: SAMPLE_METHOD_LIVE,
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: True,
                CONF_ENABLED: True,
            },
        )
        assert result["step_id"] == "capture"
        assert "temporary-capture-id" in result["description_placeholders"]["capture_url"]

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["step_id"] == "capture"
        assert result["errors"]["base"] == "no_payload_received"

        client: TestClient = await hass_client()
        response = await client.post(
            "/api/webhook/temporary-capture-id",
            json={"temperature": 22.5, "online": True},
        )
        assert response.status == HTTPStatus.OK
        assert await response.json() == {"ok": True, "captured": True, "fields": 2}

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["step_id"] == "select_fields"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={CONF_FIELDS: ["/temperature"]}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={FIELD_NAME: "Temperature", FIELD_PLATFORM: PLATFORM_SENSOR},
        )
        assert result["step_id"] == "confirm"
        assert "final-webhook-id" in result["description_placeholders"]["webhook_url"]
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    source = next(iter(parent.subentries.values()))
    assert source.data[CONF_WEBHOOK_ID] == "final-webhook-id"
    assert source.data[CONF_FIELDS][0][FIELD_PATH] == "/temperature"
    assert CONF_SAMPLE_PAYLOAD not in source.data


async def test_live_capture_rejects_bad_or_container_only_payloads(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Only a valid JSON payload with selectable scalar leaves completes capture."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")

    with patch(
        "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
        side_effect=["final-webhook", "capture-webhook"],
    ):
        result = await _start_source_flow(hass, parent)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Live source",
                CONF_SAMPLE_METHOD: SAMPLE_METHOD_LIVE,
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: True,
                CONF_ENABLED: True,
            },
        )
        client: TestClient = await hass_client()

        response = await client.post("/api/webhook/capture-webhook", data=b"{bad")
        assert response.status == HTTPStatus.BAD_REQUEST
        assert (await response.json())["error"] == "invalid_json"

        response = await client.post("/api/webhook/capture-webhook", json={})
        assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert (await response.json())["error"] == "no_scalar_fields"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["step_id"] == "capture"
        assert result["errors"]["base"] == "no_payload_received"
        hass.config_entries.subentries.async_abort(result["flow_id"])


async def test_nonlocal_source_prefers_nabu_casa_cloudhook(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Local-only off should surface a real cloudhook when HA Cloud is available."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")
    hass.config.components.add("cloud")
    cloud = _fake_cloud(cloudhook_url="https://hooks.nabu.casa/example")

    with (
        patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
            return_value="cloud-webhook-id",
        ),
        patch(
            "custom_components.http_data_bridge.webhooks._get_cloud_component",
            return_value=cloud,
        ),
    ):
        result = await _enter_pasted_sample(
            hass,
            parent,
            name="Remote source",
            payload='{"value":1}',
            stale_after=0,
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={CONF_FIELDS: ["/value"]}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={FIELD_NAME: "Value", FIELD_PLATFORM: PLATFORM_SENSOR},
        )

        assert result["step_id"] == "confirm"
        assert result["description_placeholders"]["webhook_url"] == (
            "https://hooks.nabu.casa/example"
        )
        cloud.async_get_or_create_cloudhook.assert_awaited_once_with(
            hass, "cloud-webhook-id"
        )

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    source = next(iter(parent.subentries.values()))
    assert source.data[CONF_CLOUDHOOK_URL] == "https://hooks.nabu.casa/example"


async def test_local_only_source_does_not_create_cloudhook(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """The simple local-only switch remains authoritative over cloud availability."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")
    hass.config.components.add("cloud")

    with (
        patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
            return_value="local-webhook-id",
        ),
        patch(
            "custom_components.http_data_bridge.webhooks._get_cloud_component"
        ) as get_cloud,
        patch(
            "custom_components.http_data_bridge.webhooks.webhook.async_generate_url",
            return_value="http://192.168.1.2:8123/api/webhook/local-webhook-id",
        ),
    ):
        result = await _enter_pasted_sample(
            hass,
            parent,
            name="LAN source",
            payload='{"value":1}',
            stale_after=0,
            local_only=True,
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={CONF_FIELDS: ["/value"]}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={FIELD_NAME: "Value", FIELD_PLATFORM: PLATFORM_SENSOR},
        )

    assert "192.168.1.2" in result["description_placeholders"]["webhook_url"]
    get_cloud.assert_not_called()


async def test_settings_only_reconfigure_preserves_ids_and_mappings(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """A settings-only source reconfigure should preserve stable identity."""
    parent = entry_factory()
    source = parent.subentries["test-source-subentry"]
    original_fields = list(source.data[CONF_FIELDS])

    with patch(
        "custom_components.http_data_bridge.config_flow.async_resolve_webhook_url",
        new_callable=AsyncMock,
        return_value=("https://ha.example/api/webhook/test", None, False),
    ):
        result = await parent.start_subentry_reconfigure_flow(
            hass, source.subentry_id
        )
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Renamed source",
                CONF_SAMPLE_METHOD: SAMPLE_METHOD_KEEP,
                CONF_STALE_AFTER: 300,
                CONF_LOCAL_ONLY: True,
                CONF_ENABLED: True,
            },
        )
        assert result["step_id"] == "confirm_existing"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.ABORT
    updated = parent.subentries[source.subentry_id]
    assert updated.title == "Renamed source"
    assert updated.data[CONF_SOURCE_ID] == "test-source-id"
    assert updated.data[CONF_WEBHOOK_ID] == "test-http-data-bridge-webhook"
    assert updated.data[CONF_FIELDS] == original_fields
    assert updated.data[CONF_STALE_AFTER] == 300
    assert updated.data[CONF_LOCAL_ONLY] is True


async def test_live_reconfigure_uses_temporary_webhook_and_preserves_real_id(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Live rediscovery must not collide with an already registered source webhook."""
    parent = entry_factory()
    source = parent.subentries["test-source-subentry"]
    assert await hass.config_entries.async_setup(parent.entry_id)
    await hass.async_block_till_done()

    with patch(
        "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
        return_value="reconfigure-capture-webhook",
    ):
        result = await parent.start_subentry_reconfigure_flow(hass, source.subentry_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Test source",
                CONF_SAMPLE_METHOD: SAMPLE_METHOD_LIVE,
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: True,
                CONF_ENABLED: True,
            },
        )
        assert result["step_id"] == "capture"
        assert "reconfigure-capture-webhook" in result["description_placeholders"]["capture_url"]

        client: TestClient = await hass_client()
        response = await client.post(
            "/api/webhook/reconfigure-capture-webhook", json={"new_value": 7}
        )
        assert response.status == HTTPStatus.OK
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )
        assert result["step_id"] == "select_fields"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={CONF_FIELDS: ["/new_value"]}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={FIELD_NAME: "New value", FIELD_PLATFORM: PLATFORM_SENSOR},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    updated = parent.subentries[source.subentry_id]
    assert updated.data[CONF_WEBHOOK_ID] == "test-http-data-bridge-webhook"
    assert updated.data[CONF_FIELDS][0][FIELD_PATH] == "/new_value"


async def test_invalid_pasted_samples_are_recoverable(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Malformed, non-finite, and pathologically nested samples stay on sample form."""
    parent = entry_factory()
    hass.config_entries.async_remove_subentry(parent, "test-source-subentry")

    for index, sample in enumerate(
        ("{not-json}", '{"value":1e400}', "[" * 2000 + "0" + "]" * 2000)
    ):
        with patch(
            "custom_components.http_data_bridge.config_flow.webhook.async_generate_id",
            return_value=f"invalid-webhook-{index}",
        ):
            result = await _start_source_flow(hass, parent)
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"],
                user_input={
                    CONF_SOURCE_NAME: "Invalid",
                    CONF_SAMPLE_METHOD: SAMPLE_METHOD_PASTE,
                    CONF_STALE_AFTER: 0,
                    CONF_LOCAL_ONLY: False,
                    CONF_ENABLED: True,
                },
            )
            assert result["step_id"] == "sample"
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], user_input={CONF_SAMPLE_PAYLOAD: sample}
            )
            assert result["step_id"] == "sample"
            assert result["errors"][CONF_SAMPLE_PAYLOAD] == "invalid_json"
            hass.config_entries.subentries.async_abort(result["flow_id"])


async def test_reconfigure_remote_source_to_local_removes_cloudhook_marker(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Local-only should immediately stop advertising/relying on an old cloudhook."""
    parent = entry_factory(
        extra_source_data={CONF_CLOUDHOOK_URL: "https://hooks.nabu.casa/old"}
    )
    source = parent.subentries["test-source-subentry"]

    with (
        patch(
            "custom_components.http_data_bridge.config_flow.async_resolve_webhook_url",
            new_callable=AsyncMock,
            return_value=("http://ha.local/api/webhook/test", None, False),
        ),
        patch(
            "custom_components.http_data_bridge.config_flow.async_delete_cloudhook",
            new_callable=AsyncMock,
            return_value=True,
        ) as delete_cloudhook,
    ):
        result = await parent.start_subentry_reconfigure_flow(hass, source.subentry_id)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_SOURCE_NAME: "Test source",
                CONF_SAMPLE_METHOD: SAMPLE_METHOD_KEEP,
                CONF_STALE_AFTER: 0,
                CONF_LOCAL_ONLY: True,
                CONF_ENABLED: True,
            },
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.ABORT
    delete_cloudhook.assert_awaited_once_with(
        hass, "test-http-data-bridge-webhook"
    )
    updated = parent.subentries[source.subentry_id]
    assert CONF_CLOUDHOOK_URL not in updated.data
    assert updated.data[CONF_LOCAL_ONLY] is True
