# HTTP Data Bridge

Turn incoming JSON webhook data into native Home Assistant entities through a guided UI.

HTTP Data Bridge is a custom Home Assistant integration for applications, scripts, websites, and devices that can **push JSON over HTTP** but do not have their own Home Assistant integration or MQTT support.

Instead of building webhook automations, helpers, and templates by hand, you add a push source, send one representative request (or paste example JSON), select the values you care about, and HTTP Data Bridge creates normal Home Assistant entities for them.

## What it does

- Uses **one HTTP Data Bridge integration entry with multiple push sources** underneath it.
- Creates a unique Home Assistant webhook for each source.
- Uses a guided setup flow; no YAML is required.
- Can discover the payload structure from a **real test request** or pasted example JSON.
- Accepts nested JSON objects and arrays.
- Shows every scalar leaf value in the sample payload and lets you select which ones to expose.
- Creates normal `sensor` entities for strings/numbers/null values.
- Can create `binary_sensor` entities from JSON `true` / `false` values.
- Lets you assign an optional unit of measurement to numeric sensors.
- Generates ready-to-copy sender examples for cURL, JavaScript, and PHP.
- Stores only the **selected values**, not the complete incoming payload.
- Restores the last selected values after Home Assistant restarts.
- Can mark entities unavailable after a configurable period without a new payload.
- Can restrict a source to requests from the local network.
- When local-only is disabled and Home Assistant Cloud is available, automatically creates and shows a **Nabu Casa cloudhook URL**.
- Adds a diagnostic **Last received** timestamp sensor per source.
- Provides an admin-only management panel showing source health, freshness, mapped values, and webhook endpoints.
- Keeps the same source identity, webhook ID, entities, and persisted values when a source is reconfigured.

## Multiple sources

HTTP Data Bridge is configured once in Home Assistant. Individual producers are then added as **sources**:

```text
HTTP Data Bridge
├── Driving website
├── Home Intelligence
├── Backup monitor
└── Other application
```

Each source has its own webhook, entity mappings, stale timeout, local-only setting, and persisted values.

To add another source, open **Settings → Devices & services → HTTP Data Bridge** and choose **Add source**.

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

During source setup, HTTP Data Bridge shows:

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

Every later POST to that source's generated webhook updates those entities immediately.

## Installation

### HACS custom repository

1. Open HACS.
2. Add `https://github.com/conorod1992/http_data_bridge` as a custom repository of type **Integration**.
3. Install **HTTP Data Bridge**.
4. Restart Home Assistant.

### Manual

Copy `custom_components/http_data_bridge` into `/config/custom_components/http_data_bridge` and restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **HTTP Data Bridge**.
4. The integration creates its parent entry and opens the first **Add source** flow.
5. Give the source a friendly name and configure the stale/local-only/enabled settings.
6. Choose how HTTP Data Bridge should learn the payload:
   - **Capture a live request** (default) — Home Assistant gives you a temporary setup webhook. Send one representative JSON POST to it, then continue.
   - **Paste example JSON** — paste a representative payload directly into the setup form.
7. Select the JSON values you want to expose.
8. Configure each selected value.
9. Copy the generated **permanent** webhook URL and one of the sender examples.
10. Save the source and begin POSTing JSON.

The captured or pasted example payload is only kept while the setup flow is open. It is **not** saved in the source configuration. Live discovery uses a separate temporary webhook, which is removed when setup continues or is abandoned; it never replaces the source's permanent webhook ID.

## Management panel

HTTP Data Bridge adds an admin-only sidebar panel. It provides a compact view of all configured sources, including:

- whether each source is receiving data, stale, waiting, disabled, or unloaded;
- the last received time and configured stale timeout;
- the currently selected/mapped values and their availability;
- whether the endpoint is local-only, a Nabu Casa cloudhook, or a normal Home Assistant webhook URL;
- a masked **Show / Copy** control for the webhook URL.

The panel only exposes values you deliberately mapped. Unselected fields from incoming payloads are not retained for the panel.

## Local and remote webhook URLs

The **Only allow local network requests** switch is the user-facing control for webhook reachability.

- When enabled, HTTP Data Bridge uses a normal Home Assistant webhook URL intended for local access and Home Assistant rejects remote/cloud requests to that webhook.
- When disabled, HTTP Data Bridge automatically uses a Nabu Casa cloudhook when Home Assistant Cloud is active and connected.
- If Home Assistant Cloud is unavailable, it falls back to Home Assistant's normal webhook URL using the configured external URL where possible.

There is no separate “cloudhook mode” to configure. If a cloudhook is used, its URL is persisted and reused across reloads instead of being casually regenerated.

## Sending data

Send an HTTP `POST` request to the generated webhook URL with a JSON body.

Example:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  --data '{"temperature":21.4,"online":true}' \
  'https://hooks.nabu.casa/YOUR_SECRET_CLOUDHOOK'
```

A valid request returns:

```json
{"ok": true}
```

Malformed JSON returns HTTP `400`. Payloads larger than 256 KiB return HTTP `413`.

### Important: payloads are snapshots

Each accepted POST is treated as the latest snapshot from that source.

If a configured field is missing from the newest payload, that entity becomes unavailable until a later payload contains the field again. This avoids silently leaving an old value visible when the sender has stopped supplying it.

## Nested data and arrays

Nested objects work automatically. Arrays are indexed, for example `rooms[0].temperature`.

Array indexes are positional. If the order of an array changes between payloads, an index such as `[0]` may refer to a different item. For data with changing order, it is better for the sender to use stable object keys.

## Reconfiguring

Open the HTTP Data Bridge integration entry and reconfigure the desired source.

You can change the source name, stale timeout, local-only setting, or enabled state while keeping the existing entity mappings. To replace the mappings, either capture a new live request or paste a new example payload and select the desired fields again.

Live rediscovery uses a separate temporary webhook, so a currently running source remains on its existing endpoint throughout reconfiguration. The source ID and permanent webhook ID are deliberately preserved.

## Upgrading from v0.1.x

v0.1.x stored each push source as a separate Home Assistant config entry. v0.2 automatically consolidates those entries beneath one **HTTP Data Bridge** parent entry.

The migration deliberately preserves each source's previous config-entry ID as its stable source ID. This allows existing webhook IDs, entity unique IDs, device identifiers, entity history, and persisted last values to remain associated with the same logical source.

A previously disabled v0.1 source remains disabled after migration.

## Security

A Home Assistant webhook/cloudhook URL contains a long random secret.

- Do not publish or log the complete URL unnecessarily.
- Anyone who has the complete URL can submit data to that source.
- Enable **Only allow local network requests** if the sender is entirely local.
- The management panel is restricted to Home Assistant administrators.
- Only values explicitly selected during setup are persisted. Unselected fields in incoming payloads are not written to HTTP Data Bridge storage.
- Live-capture sample payloads exist only in the active setup flow and are not copied into source configuration or panel storage.

## Data types

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

The integration is currently push-only. It does not yet:

- poll REST APIs;
- expose structured payloads as a timestamp/payload entity;
- receive form-encoded payloads;
- transform values with regex/templates;
- map arbitrary strings such as `"ON"` / `"OFF"` into binary sensors;
- create entities dynamically from previously unseen fields;
- provide authentication in addition to the secret webhook URL.

## License

MIT
