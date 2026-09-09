"""Unit tests for HTTP Data Bridge payload helpers."""

from custom_components.http_data_bridge.helpers import (
    get_by_pointer,
    iter_scalar_fields,
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


def test_root_scalar_uses_empty_pointer() -> None:
    """A scalar JSON document should still be mappable."""
    assert list(iter_scalar_fields(42)) == [("", 42)]
    assert get_by_pointer(42, "") == 42
    assert pointer_to_label("") == "$"
    assert suggested_name("") == "Value"


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
    assert sample_display("x" * 100, limit=10) == "xxxxxxxxx…"
    assert normalise_sensor_value(True) == "on"
    assert normalise_sensor_value(False) == "off"
    assert normalise_sensor_value(None) is None
    assert normalise_sensor_value(12.5) == 12.5
