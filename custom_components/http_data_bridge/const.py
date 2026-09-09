"""Constants for HTTP Data Bridge."""

DOMAIN = "http_data_bridge"
PARENT_TITLE = "HTTP Data Bridge"
SUBENTRY_TYPE_SOURCE = "source"

CONF_CLOUDHOOK_URL = "cloudhook_url"
CONF_ENABLED = "enabled"
CONF_FIELDS = "fields"
CONF_LOCAL_ONLY = "local_only"
CONF_SAMPLE_PAYLOAD = "sample_payload"
CONF_SOURCE_ID = "source_id"
CONF_SOURCE_NAME = "source_name"
CONF_STALE_AFTER = "stale_after"
CONF_WEBHOOK_ID = "webhook_id"

FIELD_NAME = "name"
FIELD_PATH = "path"
FIELD_PLATFORM = "platform"
FIELD_UNIT = "unit"

PLATFORM_SENSOR = "sensor"
PLATFORM_BINARY_SENSOR = "binary_sensor"

DEFAULT_STALE_AFTER = 0
MAX_PAYLOAD_BYTES = 256 * 1024
STORAGE_SAVE_DELAY = 10
STORAGE_VERSION = 1
