"""Constants for HTTP Data Bridge."""

from __future__ import annotations

DOMAIN = "http_data_bridge"
NAME = "HTTP Data Bridge"

CONF_SOURCE_NAME = "source_name"
CONF_FIELDS = "fields"
CONF_SAMPLE_PAYLOAD = "sample_payload"
CONF_STALE_AFTER = "stale_after"
CONF_LOCAL_ONLY = "local_only"
CONF_WEBHOOK_ID = "webhook_id"

FIELD_PATH = "path"
FIELD_NAME = "name"
FIELD_PLATFORM = "platform"
FIELD_UNIT = "unit"

PLATFORM_SENSOR = "sensor"
PLATFORM_BINARY_SENSOR = "binary_sensor"

DEFAULT_STALE_AFTER = 0
MAX_PAYLOAD_BYTES = 256 * 1024

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 1.0
