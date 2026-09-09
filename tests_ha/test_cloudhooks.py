"""Cloudhook URL/lifecycle tests for HTTP Data Bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant

from custom_components.http_data_bridge.const import (
    CONF_CLOUDHOOK_URL,
    CONF_LOCAL_ONLY,
    CONF_WEBHOOK_ID,
)
from custom_components.http_data_bridge.webhooks import async_resolve_webhook_url


def _fake_cloud(*, connected: bool = True, cloudhook_url: str | None = None) -> Mock:
    """Return the small Home Assistant Cloud surface the integration needs."""
    cloud = Mock()
    cloud.CloudNotAvailable = RuntimeError
    cloud.async_active_subscription.return_value = True
    cloud.async_is_connected.return_value = connected
    cloud.async_get_or_create_cloudhook = AsyncMock(return_value=cloudhook_url)
    cloud.async_delete_cloudhook = AsyncMock()
    return cloud


async def test_remote_url_falls_back_when_cloud_disconnected(
    hass: HomeAssistant,
) -> None:
    """Cloud being temporarily unavailable should not prevent source setup."""
    hass.config.components.add("cloud")
    cloud = _fake_cloud(connected=False)
    with (
        patch(
            "custom_components.http_data_bridge.webhooks._get_cloud_component",
            return_value=cloud,
        ),
        patch(
            "custom_components.http_data_bridge.webhooks.webhook.async_generate_url",
            return_value="https://ha.example/api/webhook/example",
        ) as generate_url,
    ):
        url, cloudhook, created = await async_resolve_webhook_url(
            hass,
            {CONF_WEBHOOK_ID: "example", CONF_LOCAL_ONLY: False},
        )

    assert url == "https://ha.example/api/webhook/example"
    assert cloudhook is None
    assert created is False
    generate_url.assert_called_once()
    cloud.async_get_or_create_cloudhook.assert_not_awaited()


async def test_stored_cloudhook_is_reused_without_recreation(
    hass: HomeAssistant,
) -> None:
    """A persisted Nabu Casa URL should be stable across reloads."""
    hass.config.components.add("cloud")
    cloud = _fake_cloud()
    with patch(
        "custom_components.http_data_bridge.webhooks._get_cloud_component",
        return_value=cloud,
    ):
        url, cloudhook, created = await async_resolve_webhook_url(
            hass,
            {
                CONF_WEBHOOK_ID: "example",
                CONF_LOCAL_ONLY: False,
                CONF_CLOUDHOOK_URL: "https://hooks.nabu.casa/stable",
            },
        )

    assert url == "https://hooks.nabu.casa/stable"
    assert cloudhook == url
    assert created is False
    cloud.async_get_or_create_cloudhook.assert_not_awaited()


async def test_cloud_module_not_imported_when_cloud_component_is_absent(
    hass: HomeAssistant,
) -> None:
    """Normal webhook sources must not require Home Assistant Cloud dependencies."""
    with (
        patch(
            "custom_components.http_data_bridge.webhooks._get_cloud_component"
        ) as get_cloud,
        patch(
            "custom_components.http_data_bridge.webhooks.webhook.async_generate_url",
            return_value="https://ha.example/api/webhook/example",
        ),
    ):
        url, cloudhook, created = await async_resolve_webhook_url(
            hass,
            {CONF_WEBHOOK_ID: "example", CONF_LOCAL_ONLY: False},
        )

    assert url == "https://ha.example/api/webhook/example"
    assert cloudhook is None
    assert created is False
    get_cloud.assert_not_called()
