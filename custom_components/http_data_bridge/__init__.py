"""HTTP Data Bridge integration."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_ENABLED,
    CONF_LOCAL_ONLY,
    CONF_SOURCE_ID,
    CONF_WEBHOOK_ID,
    DOMAIN,
    PARENT_TITLE,
    SUBENTRY_TYPE_SOURCE,
)
from .data import HttpDataBridgeManager, async_remove_storage
from .frontend import async_register_frontend, async_remove_frontend_panel
from .frontend_management import async_cleanup_management_drafts
from .webhooks import async_delete_cloudhook

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)
_MIGRATION_REMOVALS = "migration_removals"


def _migration_removals(hass: HomeAssistant) -> set[str]:
    """Return config-entry ids being removed only because they were consolidated."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(_MIGRATION_REMOVALS, set())


def _legacy_source_data(entry: ConfigEntry) -> dict[str, Any]:
    """Convert a v0.1 config entry into source-subentry data."""
    return {
        **dict(entry.data),
        CONF_SOURCE_ID: entry.entry_id,
        CONF_ENABLED: entry.disabled_by is None,
    }


def _move_registry_ownership(
    hass: HomeAssistant,
    legacy_entry: ConfigEntry,
    parent_entry: ConfigEntry,
    subentry: ConfigSubentry,
) -> None:
    """Move legacy entities/devices under the new source subentry without new IDs."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    for entity in er.async_entries_for_config_entry(entity_registry, legacy_entry.entry_id):
        disabled_by = entity.disabled_by
        if (
            legacy_entry.disabled_by is not None
            and disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY
        ):
            # The migrated source has its own CONF_ENABLED flag. Carrying the old
            # config-entry disable into the entity registry as USER would make the
            # entity stay disabled even after the source itself is later enabled.
            disabled_by = None
        entity_registry.async_update_entity(
            entity.entity_id,
            config_entry_id=parent_entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            disabled_by=disabled_by,
        )

    for device in dr.async_entries_for_config_entry(device_registry, legacy_entry.entry_id):
        disabled_by = device.disabled_by
        if (
            legacy_entry.disabled_by is not None
            and disabled_by is dr.DeviceEntryDisabler.CONFIG_ENTRY
        ):
            # Source-level enablement now owns this state; avoid converting a
            # temporary config-entry disable into a sticky user device disable.
            disabled_by = None
        device_registry.async_update_device(
            device.id,
            new_config_entry_id=parent_entry.entry_id,
            new_config_subentry_id=subentry.subentry_id,
            disabled_by=disabled_by,
        )


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up HTTP Data Bridge, frontend, and legacy-entry consolidation."""
    await async_register_frontend(hass)
    await async_migrate_integration(hass)
    # Migration can create the first v2 parent after frontend registration.
    await async_register_frontend(hass)
    return True


async def async_migrate_integration(hass: HomeAssistant) -> None:
    """Consolidate v0.1 one-source-per-entry installs into one parent entry."""
    legacy_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.version == 1
    ]
    if not legacy_entries:
        return

    existing_parents = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.version >= 2
    ]
    legacy_entries.sort(key=lambda item: item.disabled_by is not None)
    parent = existing_parents[0] if existing_parents else legacy_entries[0]

    existing_source_ids = {
        str(subentry.data.get(CONF_SOURCE_ID, ""))
        for subentry in parent.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_SOURCE
    }

    for legacy in legacy_entries:
        source_id = legacy.entry_id
        if source_id in existing_source_ids:
            continue

        subentry = ConfigSubentry(
            data=MappingProxyType(_legacy_source_data(legacy)),
            subentry_type=SUBENTRY_TYPE_SOURCE,
            title=legacy.title,
            unique_id=None,
        )
        hass.config_entries.async_add_subentry(parent, subentry)
        existing_source_ids.add(source_id)
        _move_registry_ownership(hass, legacy, parent, subentry)

    hass.config_entries.async_update_entry(
        parent,
        title=PARENT_TITLE,
        data={},
        options={},
        version=2,
    )

    protected = _migration_removals(hass)
    for legacy in legacy_entries:
        if legacy.entry_id == parent.entry_id:
            continue
        protected.add(legacy.entry_id)
        await hass.config_entries.async_remove(legacy.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Fallback migration for a v1 entry not consolidated during component setup."""
    if entry.version != 1:
        return True

    subentry = ConfigSubentry(
        data=MappingProxyType(_legacy_source_data(entry)),
        subentry_type=SUBENTRY_TYPE_SOURCE,
        title=entry.title,
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    _move_registry_ownership(hass, entry, entry, subentry)
    hass.config_entries.async_update_entry(
        entry,
        title=PARENT_TITLE,
        data={},
        options={},
        version=2,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the parent entry and all configured push-source subentries."""
    manager = HttpDataBridgeManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await manager.async_shutdown()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # The component may have been loaded only to present the initial config flow,
    # before a parent entry existed. Register the panel now that setup succeeded.
    await async_register_frontend(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all HTTP Data Bridge source entities and webhooks."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    manager: HttpDataBridgeManager = entry.runtime_data
    await manager.async_shutdown()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the parent when a source subentry is added, changed, or removed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted source data, cloudhooks, drafts, and panel on parent deletion."""
    protected = _migration_removals(hass)
    if entry.entry_id in protected:
        protected.discard(entry.entry_id)
        return

    # A browser can be closed in the middle of a pasted/live setup wizard. Drafts
    # normally expire automatically, but deleting the integration should remove
    # temporary capture webhooks/cloudhooks and cancel their expiry timers now.
    await async_cleanup_management_drafts(hass)

    if entry.version == 1:
        await async_remove_storage(hass, entry.entry_id)
        webhook_id = entry.data.get(CONF_WEBHOOK_ID)
        if webhook_id and entry.data.get(CONF_CLOUDHOOK_URL):
            await async_delete_cloudhook(hass, str(webhook_id))
        async_remove_frontend_panel(hass)
        return

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SOURCE:
            continue
        source_id = str(subentry.data.get(CONF_SOURCE_ID, ""))
        if source_id:
            await async_remove_storage(hass, source_id)
        webhook_id = subentry.data.get(CONF_WEBHOOK_ID)
        if webhook_id and (
            subentry.data.get(CONF_CLOUDHOOK_URL)
            or not bool(subentry.data.get(CONF_LOCAL_ONLY, False))
        ):
            await async_delete_cloudhook(hass, str(webhook_id))

    async_remove_frontend_panel(hass)
