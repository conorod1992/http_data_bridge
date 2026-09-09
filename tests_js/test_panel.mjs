import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const registry = new Map();

globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {
      addEventListener() {},
      innerHTML: "",
    };
  }
};
globalThis.customElements = {
  define(name, constructor) {
    registry.set(name, constructor);
  },
  get(name) {
    return registry.get(name);
  },
};
globalThis.HTMLInputElement = class {};
globalThis.HTMLTextAreaElement = class {};
globalThis.HTMLSelectElement = class {};
globalThis.Element = class {};
globalThis.document = { visibilityState: "visible" };

const panelPath = "custom_components/http_data_bridge/frontend/http-data-bridge-panel.js";
vm.runInThisContext(fs.readFileSync(panelPath, "utf8"), { filename: panelPath });

const Panel = customElements.get("http-data-bridge-panel");
assert.ok(Panel, "panel custom element should register");
const panel = new Panel();

panel._editor = {
  existing: [],
  mappings: new Map(),
  nodes: [
    { path: "", label: "$", kind: "object", preview: "object (3 keys)", suggested_name: "Payload", is_boolean: false, is_number: false, requires_attribute: true },
    { path: "/details", label: "details", kind: "object", preview: "object (1 key)", suggested_name: "Details", is_boolean: false, is_number: false, requires_attribute: true },
    { path: "/details/room", label: "details.room", kind: "string", preview: "kitchen", suggested_name: "Room", is_boolean: false, is_number: false, requires_attribute: false },
    { path: "/items", label: "items", kind: "array", preview: "array (1 item)", suggested_name: "Items", is_boolean: false, is_number: false, requires_attribute: true },
    { path: "/items/0", label: "items[0]", kind: "object", preview: "object (1 key)", suggested_name: "Item 0", is_boolean: false, is_number: false, requires_attribute: true },
    { path: "/items/0/name", label: "items[0].name", kind: "string", preview: "Alpha", suggested_name: "Name", is_boolean: false, is_number: false, requires_attribute: false },
    { path: "/online", label: "online", kind: "boolean", preview: "true", suggested_name: "Online", is_boolean: true, is_number: false, requires_attribute: false },
  ],
};

const byPath = new Map(panel._editor.nodes.map((node) => [node.path, node]));
assert.equal(panel._friendlyName(byPath.get("")), "Entire request");
assert.equal(panel._friendlyName(byPath.get("/details")), "Details");
assert.equal(panel._friendlyPreview(byPath.get("/details")), "Group containing 1 value");
assert.equal(panel._friendlyPreview(byPath.get("/items")), "List containing 1 item");
assert.equal(panel._friendlyName(byPath.get("/items/0")), "Item 1");
assert.equal(panel._friendlyPreview(byPath.get("/online")), "Yes");
assert.deepEqual(panel._children("/details").map((node) => node.path), ["/details/room"]);

const mappingHtml = panel._mappingsStep();
assert.match(mappingHtml, /Choose what you want in Home Assistant/);
assert.match(mappingHtml, /Group containing 1 value/);
assert.match(mappingHtml, /List containing 1 item/);
assert.match(mappingHtml, /Advanced options/);
assert.match(mappingHtml, /Use the entire request as one sensor/);
assert.doesNotMatch(mappingHtml, />\$</);
assert.doesNotMatch(mappingHtml, /object \(1 key\)/);

const containerMapping = panel._defaultMap(byPath.get("/details"));
assert.equal(containerMapping.platform, "sensor");
assert.equal(containerMapping.store_in_attribute, true);

const builder = panel._newBuilder("https://example.test/webhook");
builder.rows = [
  { id: "1", name: "temperature", type: "number", value: "21.5" },
  { id: "2", name: "online", type: "boolean", value: "true" },
  { id: "3", name: "status", type: "text", value: "running" },
];
assert.deepEqual(panel._builderPayload(builder), {
  value: { temperature: 21.5, online: true, status: "running" },
  error: "",
});

const codeExpectations = {
  powershell: "Invoke-RestMethod",
  curl: "curl -X POST",
  javascript: "fetch(",
  php: "curl_init",
};
for (const [tab, expected] of Object.entries(codeExpectations)) {
  builder.tab = tab;
  const generated = panel._builderCode(builder);
  assert.equal(generated.error, "");
  assert.match(generated.code, /https:\/\/example\.test\/webhook/);
  assert.match(generated.code, /temperature/);
  assert.match(generated.code, /21\.5/);
  assert.ok(generated.code.includes(expected), `${tab} should contain ${expected}`);
}

builder.mode = "json";
builder.json = '{"nested":{"value":42},"items":[1,2]}';
assert.deepEqual(panel._builderPayload(builder), {
  value: { nested: { value: 42 }, items: [1, 2] },
  error: "",
});

builder.json = "{invalid";
assert.equal(panel._builderPayload(builder).error, "Enter valid JSON to generate code.");

console.log("HTTP Data Bridge panel helper tests passed");
