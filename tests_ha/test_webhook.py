"""Runtime webhook and entity tests for HTTP Data Bridge."""

from __future__ import annotations

from http import HTTPStatus

from aiohttp.test_utils import TestClient

from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.http_data_bridge.const import (
    DOMAIN,
    FIELD_NAME,
    FIELD_PATH,
    FIELD_PLATFORM,
    MAX_PAYLOAD_BYTES,
    PLATFORM_SENSOR,
)


async def _setup_source(hass: HomeAssistant, entry_factory, **entry_kwargs):
    entry = entry_factory(**entry_kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, platform: str, source_id: str, path: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{source_id}:{path}")
    assert entity_id is not None
    return entity_id


async def test_webhook_updates_native_entities_and_snapshot_availability(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """A POST should update selected entities and missing fields go unavailable."""
    entry = await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = "/api/webhook/test-http-data-bridge-webhook"

    temperature_id = _entity_id(hass, "sensor", "test-source-id", "/temperature")
    online_id = _entity_id(hass, "binary_sensor", "test-source-id", "/online")
    assert hass.states.get(temperature_id).state == "unavailable"
    assert hass.states.get(online_id).state == "unavailable"

    response = await client.post(path, json={"temperature": 21.4, "online": True})
    assert response.status == HTTPStatus.OK
    assert await response.json() == {"ok": True}
    await hass.async_block_till_done()

    assert hass.states.get(temperature_id).state == "21.4"
    assert hass.states.get(temperature_id).attributes["unit_of_measurement"] == "°C"
    assert hass.states.get(online_id).state == "on"

    response = await client.post(path, json={"online": False})
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(temperature_id).state == "unavailable"
    assert hass.states.get(online_id).state == "off"


async def test_entities_are_owned_by_source_subentry(
    hass: HomeAssistant,
    entry_factory,
) -> None:
    """Native entities should be associated with their source subentry."""
    entry = await _setup_source(hass, entry_factory)
    registry = er.async_get(hass)
    entity_id = _entity_id(hass, "sensor", "test-source-id", "/temperature")
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.config_entry_id == entry.entry_id
    assert entity.config_subentry_id == "test-source-subentry"


async def test_type_changes_become_unavailable_when_mapping_requires_type(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Binary and unit-bearing numeric sensors reject incompatible scalar types."""
    await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = "/api/webhook/test-http-data-bridge-webhook"
    temperature_id = _entity_id(hass, "sensor", "test-source-id", "/temperature")
    online_id = _entity_id(hass, "binary_sensor", "test-source-id", "/online")

    response = await client.post(path, json={"temperature": "warm", "online": "yes"})
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(temperature_id).state == "unavailable"
    assert hass.states.get(online_id).state == "unavailable"


async def test_oversized_rendered_sensor_value_becomes_unavailable(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Scalars beyond Home Assistant's state limit should fail safely."""
    fields = [
        {
            FIELD_PATH: "/message",
            FIELD_NAME: "Message",
            FIELD_PLATFORM: PLATFORM_SENSOR,
        }
    ]
    await _setup_source(hass, entry_factory, fields=fields)
    client: TestClient = await hass_client()
    path = "/api/webhook/test-http-data-bridge-webhook"
    message_id = _entity_id(hass, "sensor", "test-source-id", "/message")

    for value in (
        "x" * (MAX_LENGTH_STATE_STATE + 1),
        10 ** (MAX_LENGTH_STATE_STATE + 1),
    ):
        response = await client.post(path, json={"message": value})
        assert response.status == HTTPStatus.OK
        await hass.async_block_till_done()
        assert hass.states.get(message_id).state == "unavailable"

    response = await client.post(path, json={"message": "short enough"})
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(message_id).state == "short enough"


async def test_invalid_and_oversized_payloads_are_rejected_without_updating(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Malformed/unsafe/oversized requests should not touch source state."""
    await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = "/api/webhook/test-http-data-bridge-webhook"
    temperature_id = _entity_id(hass, "sensor", "test-source-id", "/temperature")

    for payload in (
        b"{not-json}",
        b'{"temperature":1e400}',
        ("[" * 2000 + "0" + "]" * 2000).encode(),
    ):
        response = await client.post(path, data=payload)
        assert response.status == HTTPStatus.BAD_REQUEST
        assert hass.states.get(temperature_id).state == "unavailable"

    response = await client.post(path, data=b"x" * (MAX_PAYLOAD_BYTES + 1))
    assert response.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert hass.states.get(temperature_id).state == "unavailable"


async def test_only_post_is_accepted(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """The generated endpoint should not accept GET/PUT state updates."""
    await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = "/api/webhook/test-http-data-bridge-webhook"
    assert (await client.get(path)).status == HTTPStatus.METHOD_NOT_ALLOWED
    assert (await client.put(path, json={"temperature": 1})).status == (
        HTTPStatus.METHOD_NOT_ALLOWED
    )


async def test_disabled_source_does_not_register_webhook(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """A migrated/user-disabled source must not silently become active."""
    await _setup_source(hass, entry_factory, enabled=False)
    client: TestClient = await hass_client()
    temperature_id = _entity_id(hass, "sensor", "test-source-id", "/temperature")
    response = await client.post(
        "/api/webhook/test-http-data-bridge-webhook",
        json={"temperature": 25},
    )
    # HA intentionally returns 200 for an unknown/unregistered webhook.
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(temperature_id).state == "unavailable"
