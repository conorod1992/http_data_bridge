# HTTP Data Bridge

Turn incoming JSON webhook data into native Home Assistant entities through a guided UI.

HTTP Data Bridge is a custom Home Assistant integration for applications, scripts, websites, and devices that can **push JSON over HTTP** but do not have their own Home Assistant integration or MQTT support.

Instead of building webhook automations, helpers, and templates by hand, you add a push source, send one representative request (or paste example JSON), choose the values you care about, and HTTP Data Bridge creates normal Home Assistant entities for them.

## What it does

- Uses **one HTTP Data Bridge integration entry with multiple push sources** underneath it.
- Creates a unique Home Assistant webhook for each source.
- Uses a guided setup flow; no YAML is required.
- Can discover the payload structure from a **real test request** or pasted example JSON.
- Accepts nested JSON objects and arrays.
- Presents nested data as friendly expandable **groups** and **lists**, while keeping JSON paths available as optional technical details.
- Lets you select individual values, whole groups/lists, or the **entire request**.
- Creates normal `sensor` entities for strings/numbers/null values.
- Can create `binary_sensor` entities from JSON `true` / `false` values.
- Lets sensor values be stored in a **`value` attribute** when they are structured or too long for Home Assistant's 255-character state limit.
- Attribute-backed sensors use the receive timestamp as their state and exclude the `value` attribute from Recorder history.
- Lets you assign an optional unit of measurement to normal numeric sensors.
- Includes an optional **Build a test request** helper with ready-to-copy PowerShell, cURL, JavaScript, and PHP examples.
- Keeps the sender-code helper available after setup using the source's permanent webhook URL.
- Stores only the **selected values**, not unrelated incoming payload fields.
- Restores the last selected values after Home Assistant restarts.
- Can mark entities unavailable after a configurable period without a new payload.
- Can restrict a source to requests from the local network.
- When local-only is disabled and Home Assistant Cloud is available, automatically creates and shows a **Nabu Casa cloudhook URL**.
- Adds a diagnostic **Last received** timestamp sensor per source.
- Provides an admin-only management panel for adding, editing, deleting, and monitoring sources.
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

The HTTP Data Bridge sidebar panel is the primary day-to-day management surface. The native Home Assistant integration/subentry screens remain available as a fallback.

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

During source setup, HTTP Data Bridge presents `backup` as an expandable **group** containing `status` and `progress`. You can choose the individual values, use the complete `backup` group as one structured sensor, or use the entire request as one sensor from **Advanced options**.

You might choose:

- **Status** → **Backup status** (`sensor`)
- **Progress** → **Backup progress** (`sensor`, `%`)
- **Disk free gb** → **Disk free** (`sensor`, `GB`)
- **Online** → **Online** (`binary_sensor`)
- **Entire request** → **Full payload** (`sensor`, stored in its `value` attribute)

If you enable **Show technical details**, the exact JSON paths remain visible. The complete document is represented internally by the root JSON Pointer (commonly shown as `$` in user-facing JSON tools), but you do not need to understand JSON Pointer syntax to configure the integration.

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
4. Complete the initial integration setup. After that, use the **HTTP Data Bridge** sidebar panel to manage sources.
5. Choose **Add source**, give it a friendly name, and configure the stale/local-only/enabled settings.
6. Choose how HTTP Data Bridge should learn the payload:
   - **Send an example request** (default) — Home Assistant gives you a temporary setup webhook and detects one representative JSON POST.
   - **Paste example JSON** — paste a representative payload directly into the setup form.
7. If you choose **Send an example request** and do not already have sender code ready, expand **Need help sending a request?**. Add simple example values or edit JSON directly, then copy a ready-to-use PowerShell, cURL, JavaScript, or PHP example containing the temporary webhook URL.
8. After the request is received, choose what you want in Home Assistant. Individual values are shown directly; nested objects are shown as expandable **groups**, and arrays as expandable **lists**.
9. Configure selected values as Sensors or, for JSON booleans, Binary sensors. Whole groups/lists are stored as Sensor attributes automatically.
10. For ordinary Sensors, choose whether the value should be stored directly in the sensor state or in the sensor's `value` attribute. The UI explains the 255-character state limit where relevant.
11. Save the source and begin POSTing JSON to its permanent webhook URL.

The captured or pasted example payload is only kept while the setup flow is open. It is **not** saved in the source configuration. Live discovery uses a separate temporary webhook, which is removed when setup continues or is abandoned; it never replaces the source's permanent webhook ID.

### Build a test request

The live-capture screen includes an optional helper for users who know what they want to send but do not want to hand-write the HTTP request.

For simple payloads, add rows such as:

| Name | Type | Example value |
| --- | --- | --- |
| `temperature` | Number | `21.5` |
| `online` | Yes / No | Yes |
| `status` | Text | `running` |

HTTP Data Bridge previews the JSON and generates PowerShell, cURL, JavaScript, and PHP examples using the actual temporary setup URL. **Edit JSON directly** is available for nested objects, arrays, or other advanced examples.

This helper does **not** define a schema or restrict later requests. It only makes it quicker to produce one representative POST. The same sender-example tool is available from each saved source using its permanent webhook URL.

## Sensor state vs attribute storage

Home Assistant sensor states are limited to 255 characters. HTTP Data Bridge therefore gives each Sensor mapping a **Store value in an attribute** option.

With the option disabled, the sensor behaves normally:

```yaml
sensor.backup_status:
  state: running
```

With the option enabled, the sensor state is the time the selected value was received and the complete selected JSON value is available in the `value` attribute:

```yaml
sensor.full_payload:
  state: "2026-09-09T02:23:41+01:00"
  attributes:
    value:
      backup:
        status: running
        progress: 73
      online: true
```

Attribute storage is automatically required when the sample is a group/object, list/array, or already exceeds Home Assistant's state-length limit. It remains optional for ordinary scalar values.

The `value` attribute is marked as **unrecorded** so Recorder does not duplicate large/changing JSON data into history on every push. HTTP Data Bridge still stores the latest explicitly selected value in its own per-source storage so it can be restored after a Home Assistant restart.

## Management panel

HTTP Data Bridge adds an admin-only sidebar panel. It provides full source management and a compact view of configured sources, including:

- add, edit, enable/disable, and delete source controls;
- pasted-JSON or live-request mapping discovery;
- a hierarchical mapping view using friendly **group/list** terminology instead of requiring JSON knowledge;
- optional exact JSON paths/types under **Show technical details**;
- whether each source is receiving data, stale, waiting, disabled, or unloaded;
- the last received time and configured stale timeout;
- the currently selected/mapped values and their availability;
- whether a sensor uses normal state storage or attribute storage;
- whether the endpoint is local-only, a Nabu Casa cloudhook, or a normal Home Assistant webhook URL;
- masked **Show / Copy** controls for the webhook URL, with visible copy confirmation;
- **Sender examples** for generating ready-to-copy code against the permanent source URL.

Large attribute-backed values are **not** transmitted on every panel refresh. The panel fetches them only when an administrator explicitly chooses **View value**.

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

If a configured path is missing from the newest payload, that entity becomes unavailable until a later payload contains the path again. This applies to normal and attribute-backed sensors alike and avoids silently leaving an old value visible when the sender has stopped supplying it.

## Nested data and arrays

Nested objects work automatically. In the setup UI they are presented as expandable **groups**. Arrays are presented as expandable **lists**, with positional entries labelled **Item 1**, **Item 2**, and so on.

Exact technical paths remain available on demand; for example an experienced user can still see a path such as `rooms[0].temperature`.

You can expose an entire nested object/array through attribute storage instead of selecting only its leaves.

Array indexes are positional. If the order of an array changes between payloads, an index such as `[0]` may refer to a different item. For data with changing order, it is better for the sender to use stable object keys.

## Reconfiguring

Use the HTTP Data Bridge sidebar panel and choose **Edit** on a source.

You can change the source name, stale timeout, local-only setting, or enabled state while keeping the existing entity mappings. To replace the mappings, either capture a new live request or paste a new example payload and choose the desired values again.

Live rediscovery uses a separate temporary webhook, so a currently running source remains on its existing endpoint throughout reconfiguration. The source ID and permanent webhook ID are deliberately preserved.

The native Home Assistant subentry configuration flow remains available as a fallback.

Existing pre-v0.4 Sensor mappings remain normal state-backed sensors because the attribute-storage flag defaults to off when absent.

## Upgrading from v0.1.x

v0.1.x stored each push source as a separate Home Assistant config entry. v0.2 automatically consolidates those entries beneath one **HTTP Data Bridge** parent entry.

The migration deliberately preserves each source's previous config-entry ID as its stable source ID. This allows existing webhook IDs, entity unique IDs, device identifiers, entity history, and persisted last values to remain associated with the same logical source.

A previously disabled v0.1 source remains disabled after migration.

## Security

A Home Assistant webhook/cloudhook URL contains a long random secret.

- Do not publish or log the complete URL unnecessarily.
- Anyone who has the complete URL can submit data to that source.
- Enable **Only allow local network requests** if the sender is entirely local.
- The management panel and on-demand attribute-value viewer are restricted to Home Assistant administrators.
- Only values explicitly selected during setup are persisted. Unselected fields in incoming payloads are not written to HTTP Data Bridge storage.
- Choosing **Use the entire request as one sensor** explicitly opts into persisting the complete latest payload.
- Live-capture sample payloads exist only in the active setup flow and are not copied into source configuration or panel storage.

## Data types

| JSON value | Entity support |
| --- | --- |
| String | Sensor state or Sensor `value` attribute |
| Number | Sensor state or Sensor `value` attribute |
| `true` / `false` | Binary sensor, Sensor state, or Sensor `value` attribute |
| `null` | Sensor state or Sensor `value` attribute |
| Object / UI “group” | Sensor `value` attribute |
| Array / UI “list” | Sensor `value` attribute |
| Entire request | Sensor `value` attribute |

If a normal state-backed scalar later changes into an object or array, that field is treated as unavailable rather than serializing structured data into entity state. Attribute-backed sensors can hold any valid JSON value.

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
- receive form-encoded payloads;
- transform values with regex/templates;
- map arbitrary strings such as `"ON"` / `"OFF"` into binary sensors;
- create entities dynamically from previously unseen fields;
- provide authentication in addition to the secret webhook URL.

## License

MIT