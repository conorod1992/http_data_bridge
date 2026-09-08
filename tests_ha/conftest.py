"""Fixtures for HTTP Data Bridge tests against real Home Assistant."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.http_data_bridge.const import (
    CONF_FIELDS,
    CONF_LOCAL_ONLY,
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


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Allow Home Assistant to discover this repository's custom integration."""


@pytest.fixture
def entry_factory(hass: HomeAssistant) -> Callable[..., MockConfigEntry]:
    """Create an HTTP Data Bridge config entry."""

    def factory(
        *,
        title: str = "Test source",
        webhook_id: str = "test-http-data-bridge-webhook",
        stale_after: int = 0,
        local_only: bool = False,
        fields: list[dict] | None = None,
    ) -> MockConfigEntry:
        if fields is None:
            fields = [
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

        entry = MockConfigEntry(
            domain=DOMAIN,
            title=title,
            data={
                CONF_SOURCE_NAME: title,
                CONF_WEBHOOK_ID: webhook_id,
                CONF_STALE_AFTER: stale_after,
                CONF_LOCAL_ONLY: local_only,
                CONF_FIELDS: fields,
            },
        )
        entry.add_to_hass(hass)
        return entry

    return factory
