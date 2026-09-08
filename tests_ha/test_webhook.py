"""Runtime webhook tests for HTTP Data Bridge."""

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


def _entity_id(hass: HomeAssistant, platform: str, entry_id: str, path: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry_id}:{path}"
    )
    assert entity_id is not None
    return entity_id


async def test_webhook_updates_native_entities_and_snapshot_availability(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """A POST should update selected entities and missing fields should go unavailable."""
    entry = await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = f"/api/webhook/{entry.data['webhook_id']}"

    temperature_id = _entity_id(hass, "sensor", entry.entry_id, "/temperature")
    online_id = _entity_id(hass, "binary_sensor", entry.entry_id, "/online")

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


async def test_type_changes_become_unavailable_when_mapping_requires_type(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Binary and unit-bearing numeric sensors should reject incompatible scalar types."""
    entry = await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = f"/api/webhook/{entry.data['webhook_id']}"

    temperature_id = _entity_id(hass, "sensor", entry.entry_id, "/temperature")
    online_id = _entity_id(hass, "binary_sensor", entry.entry_id, "/online")

    response = await client.post(path, json={"temperature": "warm", "online": "yes"})
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()

    assert hass.states.get(temperature_id).state == "unavailable"
    assert hass.states.get(online_id).state == "unavailable"


async def test_oversized_sensor_state_becomes_unavailable(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Scalars beyond Home Assistant's rendered state limit should fail safely."""
    fields = [
        {
            FIELD_PATH: "/value",
            FIELD_NAME: "Value",
            FIELD_PLATFORM: PLATFORM_SENSOR,
        }
    ]
    entry = await _setup_source(hass, entry_factory, fields=fields)
    client: TestClient = await hass_client()
    path = f"/api/webhook/{entry.data['webhook_id']}"
    value_id = _entity_id(hass, "sensor", entry.entry_id, "/value")

    response = await client.post(
        path,
        json={"value": "x" * (MAX_LENGTH_STATE_STATE + 1)},
    )
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(value_id).state == "unavailable"

    response = await client.post(
        path,
        data='{"value":' + "9" * (MAX_LENGTH_STATE_STATE + 1) + "}",
    )
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(value_id).state == "unavailable"

    response = await client.post(path, json={"value": "short enough"})
    assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert hass.states.get(value_id).state == "short enough"


async def test_invalid_and_oversized_payloads_are_rejected_without_updating(
    hass: HomeAssistant,
    hass_client,
    entry_factory,
) -> None:
    """Malformed/unsafe/oversized requests should not touch source state."""
    entry = await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = f"/api/webhook/{entry.data['webhook_id']}"
    temperature_id = _entity_id(hass, "sensor", entry.entry_id, "/temperature")

    response = await client.post(path, data=b"{not-json}")
    assert response.status == HTTPStatus.BAD_REQUEST
    assert hass.states.get(temperature_id).state == "unavailable"

    response = await client.post(path, data=b'{"temperature":1e400}')
    assert response.status == HTTPStatus.BAD_REQUEST
    assert hass.states.get(temperature_id).state == "unavailable"

    deeply_nested = ("[" * 2000 + "0" + "]" * 2000).encode()
    response = await client.post(path, data=deeply_nested)
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
    entry = await _setup_source(hass, entry_factory)
    client: TestClient = await hass_client()
    path = f"/api/webhook/{entry.data['webhook_id']}"

    assert (await client.get(path)).status == HTTPStatus.METHOD_NOT_ALLOWED
    response = await client.put(path, json={"temperature": 1})
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
