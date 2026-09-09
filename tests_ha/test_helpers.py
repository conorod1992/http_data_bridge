"""Unit tests for HTTP Data Bridge payload helpers."""

from homeassistant.const import MAX_LENGTH_STATE_STATE

from custom_components.http_data_bridge.const import (
    FIELD_PLATFORM,
    FIELD_STORE_IN_ATTRIBUTE,
    FIELD_UNIT,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
)
from custom_components.http_data_bridge.helpers import (
    get_by_pointer,
    iter_json_nodes,
    iter_scalar_fields,
    mapping_value_is_available,
    normalise_sensor_value,
    pointer_to_label,
    sample_display,
    suggested_name,
)


def test_nested_fields_and_json_pointer_escaping() -> None:
    """Nested dicts, arrays, and escaped keys should round-trip."""
    payload = {
        "rooms": [{"name": "Kitchen", "temperature": 21.4}],
        "a/b": {"~flag": True},
    }
    fields = dict(iter_scalar_fields(payload))
    assert fields["/rooms/0/name"] == "Kitchen"
    assert fields["/rooms/0/temperature"] == 21.4
    assert fields["/a~1b/~0flag"] is True
    assert get_by_pointer(payload, "/a~1b/~0flag") is True
    assert pointer_to_label("/rooms/0/temperature") == "rooms[0].temperature"


def test_all_json_nodes_include_root_and_containers() -> None:
    """Attribute-backed setup should be able to select root, objects, and arrays."""
    payload = {"items": [{"id": 1}], "status": "ok"}
    nodes = dict(iter_json_nodes(payload))
    assert nodes[""] == payload
    assert nodes["/items"] == [{"id": 1}]
    assert nodes["/items/0"] == {"id": 1}
    assert nodes["/items/0/id"] == 1
    assert nodes["/status"] == "ok"


def test_root_scalar_uses_empty_pointer() -> None:
    """A scalar JSON document should still be mappable."""
    assert list(iter_scalar_fields(42)) == [("", 42)]
    assert get_by_pointer(42, "") == 42
    assert pointer_to_label("") == "$"
    assert suggested_name("") == "Payload"


def test_missing_path_raises_key_error() -> None:
    """Missing object members and array indexes should fail consistently."""
    payload = {"items": [1]}
    for pointer in ("/missing", "/items/2", "/items/not-an-index", "/items/-1", "/items/01"):
        try:
            get_by_pointer(payload, pointer)
        except KeyError:
            pass
        else:
            raise AssertionError(f"{pointer} did not raise KeyError")


def test_object_key_does_not_redirect_after_shape_change() -> None:
    """Object keys that resemble non-canonical indexes must not retarget arrays."""
    original = {"value": {"-1": "selected", "01": "also selected"}}
    assert get_by_pointer(original, "/value/-1") == "selected"
    assert get_by_pointer(original, "/value/01") == "also selected"
    changed = {"value": ["zero", "one"]}
    for pointer in ("/value/-1", "/value/01"):
        try:
            get_by_pointer(changed, pointer)
        except KeyError:
            pass
        else:
            raise AssertionError(f"{pointer} silently retargeted the array")


def test_display_and_sensor_normalisation() -> None:
    """Labels and sensor values should stay compact and HA-friendly."""
    assert sample_display(True) == "true"
    assert sample_display(None) == "null"
    assert sample_display({"a": 1}) == "object (1 key)"
    assert sample_display([1, 2]) == "array (2 items)"
    assert sample_display("x" * 100, limit=10) == "xxxxxxxxx…"
    assert normalise_sensor_value(True) == "on"
    assert normalise_sensor_value(False) == "off"
    assert normalise_sensor_value(None) is None
    assert normalise_sensor_value(12.5) == 12.5
    assert normalise_sensor_value({"a": 1}) is None


def test_mapping_compatibility_matches_home_assistant_entity_contract() -> None:
    """Panel/runtime helpers must agree with actual sensor availability rules."""
    normal_sensor = {FIELD_PLATFORM: PLATFORM_SENSOR}
    numeric_sensor = {FIELD_PLATFORM: PLATFORM_SENSOR, FIELD_UNIT: "°C"}
    binary_sensor = {FIELD_PLATFORM: PLATFORM_BINARY_SENSOR}
    attribute_sensor = {
        FIELD_PLATFORM: PLATFORM_SENSOR,
        FIELD_STORE_IN_ATTRIBUTE: True,
    }

    assert mapping_value_is_available(normal_sensor, "short") is True
    assert mapping_value_is_available(normal_sensor, True) is True
    assert mapping_value_is_available(normal_sensor, {"nested": 1}) is False
    assert mapping_value_is_available(
        normal_sensor, "x" * (MAX_LENGTH_STATE_STATE + 1)
    ) is False

    assert mapping_value_is_available(numeric_sensor, 21.4) is True
    assert mapping_value_is_available(numeric_sensor, "warm") is False
    assert mapping_value_is_available(numeric_sensor, True) is False

    assert mapping_value_is_available(binary_sensor, True) is True
    assert mapping_value_is_available(binary_sensor, "yes") is False

    assert mapping_value_is_available(attribute_sensor, {"nested": [1, 2]}) is True
    assert mapping_value_is_available(
        attribute_sensor, "x" * (MAX_LENGTH_STATE_STATE + 1)
    ) is True
