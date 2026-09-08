# HTTP Data Bridge

Turn incoming JSON webhook data into native Home Assistant entities through a guided UI.

HTTP Data Bridge is a custom Home Assistant integration for applications, scripts, websites, and devices that can **push JSON over HTTP** but do not have their own Home Assistant integration or MQTT support.

Instead of building webhook automations, helpers, and templates by hand, you paste an example payload, select the values you care about, and HTTP Data Bridge creates normal Home Assistant sensors for them.

## What v0.1.0 does

- Creates a unique Home Assistant webhook for each push source.
- Uses a guided config flow; no YAML is required.
- Accepts nested JSON objects and arrays.
- Shows every scalar leaf value in the sample payload and lets you select which ones to expose.
- Creates normal `sensor` entities for strings/numbers/null values.
- Can create `binary_sensor` entities from JSON `true` / `false` values.
- Lets you assign an optional unit of measurement to numeric sensors.
- Generates ready-to-copy sender examples for:
  - cURL
  - JavaScript
  - PHP
- Stores only the **selected values**, not the complete incoming payload.
- Restores the last selected values after Home Assistant restarts.
- Can mark entities unavailable after a configurable period without a new payload.
- Can optionally restrict a source to requests from the local network.
- Adds a diagnostic **Last received** timestamp sensor.
- Keeps the same webhook ID when a source is reconfigured.

## Example

Suppose an external application can send:

```json
{
  "backup": {
    "status": "running",
    "progress": 73
  },
  "disk_free_gb": 418.2,
  "online": true
}
```

During setup, HTTP Data Bridge shows:

```text
backup.status      — running
backup.progress    — 73
disk_free_gb       — 418.2
online             — true
```

You might choose:

- `backup.status` → **Backup status** (`sensor`)
- `backup.progress` → **Backup progress** (`sensor`, `%`)
- `disk_free_gb` → **Disk free** (`sensor`, `GB`)
- `online` → **Online** (`binary_sensor`)

Every later POST to the generated webhook updates those entities immediately.

## Installation

### HACS custom repository

1. Open HACS.
2. Add `https://github.com/conorod1992/http_data_bridge` as a custom repository of type **Integration**.
3. Install **HTTP Data Bridge**.
4. Restart Home Assistant.

### Manual

Copy:

```text
custom_components/http_data_bridge
```

into:

```text
/config/custom_components/http_data_bridge
```

and restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **HTTP Data Bridge**.
4. Give the source a friendly name.
5. Paste an example of the JSON your external system will send.
6. Optionally configure:
   - **Mark unavailable after** — seconds without a valid payload before its selected entities become unavailable. Use `0` to disable expiry.
   - **Only allow local network requests** — useful when the sender is on your LAN.
7. Select the JSON values you want to expose.
8. Configure each selected value.
9. Copy the generated webhook URL and one of the sender examples.
10. Save the integration and begin POSTing JSON.

The example payload is only used while the config flow is open. It is **not** saved in the config entry.

## Sending data

Send an HTTP `POST` request to the generated webhook URL with a JSON body.

Example:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  --data '{"temperature":21.4,"online":true}' \
  'https://home-assistant.example/api/webhook/YOUR_SECRET_WEBHOOK_ID'
```

A valid request returns:

```json
{"ok": true}
```

Malformed JSON returns HTTP `400`. Payloads larger than 256 KiB return HTTP `413`.

### Important: payloads are snapshots

Each accepted POST is treated as the latest snapshot from that source.

If a configured field is missing from the newest payload, that entity becomes unavailable until a later payload contains the field again.

For example, if you configured both `temperature` and `humidity`:

```json
{
  "temperature": 21.4
}
```

updates `temperature` and makes `humidity` unavailable.

This avoids silently leaving an old value visible when the sender has stopped supplying it.

## Nested data and arrays

Nested objects work automatically:

```json
{
  "weather": {
    "temperature": 12.7
  }
}
```

can expose `weather.temperature`.

Arrays are indexed:

```json
{
  "rooms": [
    {
      "name": "Kitchen",
      "temperature": 21.1
    }
  ]
}
```

can expose `rooms[0].temperature`.

Array indexes are positional. If the order of an array changes between payloads, an index such as `[0]` may refer to a different item. For data with changing order, it is better for the sender to use stable object keys.

## Reconfiguring

Open the integration entry and choose **Reconfigure**.

You can change the name, stale timeout, or local-only setting without replacing the entity mappings.

To replace the mapped fields, paste a new sample JSON payload during reconfiguration and select the desired fields again.

The webhook ID is deliberately preserved, so existing sender code does not need to change.

## Security

A Home Assistant webhook URL contains a long random ID and acts as a bearer secret.

- Do not publish or log the webhook URL unnecessarily.
- Anyone who has the complete URL can submit data to that source.
- Enable **Only allow local network requests** if the sender is entirely local.
- If an internet-hosted sender needs to reach Home Assistant, your Home Assistant instance must already be reachable at the generated URL. HTTP Data Bridge does not create a new public tunnel or cloudhook.
- Only values explicitly selected during setup are persisted. Unselected fields in incoming payloads are not written to HTTP Data Bridge storage.

## Data types

v0.1.0 intentionally keeps the mapping rules simple:

| JSON value | Entity support |
| --- | --- |
| String | Sensor |
| Number | Sensor |
| `true` / `false` | Binary sensor or sensor |
| `null` | Sensor |
| Object | Select individual child fields |
| Array | Select individual indexed fields |

If a configured scalar later changes into an object or array, that field is treated as missing rather than serializing the nested structure into entity state.

## Why use this instead of a normal Home Assistant webhook?

Native Home Assistant webhooks are excellent when an incoming request should simply **trigger an automation**.

HTTP Data Bridge is intended for a different job: making an external application's changing state appear as normal Home Assistant entities without manually creating helpers, webhook automations, and JSON templates.

## Why use this instead of MQTT?

MQTT remains the better choice for complex pub/sub systems, discovery-heavy device fleets, and applications already using an MQTT broker.

HTTP Data Bridge is aimed at the simpler case where an application can easily make an HTTP request and you want:

```text
External application → HTTP POST → Home Assistant entities
```

without adding MQTT infrastructure.

## Current limitations

v0.1.0 is push-only.

It does not currently:

- poll REST APIs;
- receive form-encoded payloads;
- transform values with regex/templates;
- map arbitrary strings such as `"ON"` / `"OFF"` into binary sensors;
- create entities dynamically from previously unseen fields;
- provide authentication in addition to the secret webhook URL.

REST/pull support can be added later without changing the core entity-mapping model.

## Development

The integration follows the current Home Assistant config-entry/webhook architecture and includes HACS and hassfest validation.

## License

MIT
