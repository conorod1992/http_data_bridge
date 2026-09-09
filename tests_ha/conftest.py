"""Fixtures for HTTP Data Bridge tests against real Home Assistant."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.http_data_bridge.const import (
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
    FIELD_UNIT,
    PARENT_TITLE,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
    SUBENTRY_TYPE_SOURCE,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Allow Home Assistant to discover this repository's custom integration."""


@pytest.fixture
def entry_factory(hass: HomeAssistant) -> Callable[..., MockConfigEntry]:
    """Create a parent entry containing one HTTP Data Bridge source subentry."""

    def factory(
        *,
        title: str = "Test source",
        source_id: str = "test-source-id",
        subentry_id: str = "test-source-subentry",
        webhook_id: str = "test-http-data-bridge-webhook",
        stale_after: int = 0,
        local_only: bool = False,
        enabled: bool = True,
        fields: list[dict] | None = None,
        extra_source_data: dict | None = None,
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

        source_data = {
            CONF_SOURCE_NAME: title,
            CONF_SOURCE_ID: source_id,
            CONF_WEBHOOK_ID: webhook_id,
            CONF_STALE_AFTER: stale_after,
            CONF_LOCAL_ONLY: local_only,
            CONF_ENABLED: enabled,
            CONF_FIELDS: fields,
        }
        if extra_source_data:
            source_data.update(extra_source_data)

        entry = MockConfigEntry(
            domain=DOMAIN,
            title=PARENT_TITLE,
            data={},
            version=2,
            subentries_data=[
                config_entries.ConfigSubentryData(
                    data=source_data,
                    subentry_id=subentry_id,
                    subentry_type=SUBENTRY_TYPE_SOURCE,
                    title=title,
                    unique_id=None,
                )
            ],
        )
        entry.add_to_hass(hass)
        return entry

    return factory
