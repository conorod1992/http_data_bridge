"""Migration tests for the one-parent/many-source architecture."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.http_data_bridge import async_migrate_integration
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
    PARENT_TITLE,
    PLATFORM_SENSOR,
    STORAGE_VERSION,
)
from custom_components.http_data_bridge.data import HttpDataBridgeRuntime


def _legacy_data(name: str, webhook_id: str) -> dict:
    return {
        CONF_SOURCE_NAME: name,
        CONF_WEBHOOK_ID: webhook_id,
        CONF_STALE_AFTER: 0,
        CONF_LOCAL_ONLY: False,
        CONF_FIELDS: [
            {
                FIELD_PATH: "/value",
                FIELD_NAME: "Value",
                FIELD_PLATFORM: PLATFORM_SENSOR,
            }
        ],
    }


async def test_multiple_v1_entries_consolidate_without_changing_source_identity(
    hass: HomeAssistant,
) -> None:
    """Legacy sources should become subentries without entity/device/storage churn."""
    first = MockConfigEntry(
        domain=DOMAIN,
        title="Website",
        data=_legacy_data("Website", "website-webhook"),
        version=1,
        entry_id="legacy-source-one",
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Backup",
        data=_legacy_data("Backup", "backup-webhook"),
        version=1,
        entry_id="legacy-source-two",
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    legacy_unique_id = "legacy-source-two:/value"
    entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        legacy_unique_id,
        config_entry=second,
        suggested_object_id="backup_value",
    )
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=second.entry_id,
        identifiers={(DOMAIN, second.entry_id)},
        name="Backup",
    )

    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{second.entry_id}")
    await store.async_save(
        {
            "values": {"/value": 42},
            "last_received": "2026-09-08T20:00:00+00:00",
        }
    )

    await async_migrate_integration(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    parent = entries[0]
    assert parent.title == PARENT_TITLE
    assert parent.version == 2
    assert parent.data == {}
    assert len(parent.subentries) == 2

    by_source_id = {
        subentry.data[CONF_SOURCE_ID]: subentry
        for subentry in parent.subentries.values()
    }
    assert set(by_source_id) == {"legacy-source-one", "legacy-source-two"}
    assert by_source_id["legacy-source-two"].data[CONF_WEBHOOK_ID] == "backup-webhook"

    moved_entity = entity_registry.async_get(entity.entity_id)
    assert moved_entity is not None
    assert moved_entity.unique_id == legacy_unique_id
    assert moved_entity.config_entry_id == parent.entry_id
    assert moved_entity.config_subentry_id == by_source_id["legacy-source-two"].subentry_id

    moved_device = device_registry.async_get(device.id)
    assert moved_device is not None
    assert moved_device.identifiers == {(DOMAIN, "legacy-source-two")}
    assert moved_device.config_entry_id == parent.entry_id
    assert moved_device.config_subentry_id == by_source_id["legacy-source-two"].subentry_id

    runtime = HttpDataBridgeRuntime(
        hass,
        parent,
        by_source_id["legacy-source-two"],
    )
    await runtime.async_load()
    assert runtime.values == {"/value": 42}
    assert runtime.last_received is not None
    await runtime.async_shutdown()


async def test_disabled_legacy_entry_becomes_reenableable_disabled_source(
    hass: HomeAssistant,
) -> None:
    """Legacy config-entry disables must not become sticky user registry disables."""
    enabled = MockConfigEntry(
        domain=DOMAIN,
        title="Enabled",
        data=_legacy_data("Enabled", "enabled-hook"),
        version=1,
        entry_id="enabled-source",
    )
    disabled = MockConfigEntry(
        domain=DOMAIN,
        title="Disabled",
        data=_legacy_data("Disabled", "disabled-hook"),
        version=1,
        entry_id="disabled-source",
        disabled_by=config_entries.ConfigEntryDisabler.USER,
    )
    enabled.add_to_hass(hass)
    disabled.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    disabled_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "disabled-source:/value",
        config_entry=disabled,
        suggested_object_id="disabled_value",
        disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY,
    )
    device_registry = dr.async_get(hass)
    disabled_device = device_registry.async_get_or_create(
        config_entry_id=disabled.entry_id,
        identifiers={(DOMAIN, disabled.entry_id)},
        name="Disabled",
        disabled_by=dr.DeviceEntryDisabler.CONFIG_ENTRY,
    )

    await async_migrate_integration(hass)
    parent = hass.config_entries.async_entries(DOMAIN)[0]
    by_source_id = {
        subentry.data[CONF_SOURCE_ID]: subentry
        for subentry in parent.subentries.values()
    }
    assert by_source_id["enabled-source"].data[CONF_ENABLED] is True
    assert by_source_id["disabled-source"].data[CONF_ENABLED] is False

    moved_entity = entity_registry.async_get(disabled_entity.entity_id)
    assert moved_entity is not None
    assert moved_entity.config_entry_id == parent.entry_id
    assert moved_entity.config_subentry_id == by_source_id["disabled-source"].subentry_id
    assert moved_entity.disabled_by is None

    moved_device = device_registry.async_get(disabled_device.id)
    assert moved_device is not None
    assert moved_device.config_entry_id == parent.entry_id
    assert moved_device.config_subentry_id == by_source_id["disabled-source"].subentry_id
    assert moved_device.disabled_by is None
