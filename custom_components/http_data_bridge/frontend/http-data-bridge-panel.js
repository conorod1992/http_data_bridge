class HttpDataBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._panel = null;
    this._data = null;
    this._busy = false;
    this._error = "";
    this._editor = null;
    this._delete = null;
    this._view = null;
    this._sender = null;
    this._revealed = new Set();
    this._expanded = new Set();
    this._technical = false;
    this._feedback = new Map();
    this._feedbackTimers = new Map();
    this._timer = null;
    this._captureTimer = null;
    this._builderCounter = 0;

    this.shadowRoot.addEventListener("click", (event) => void this._click(event));
    this.shadowRoot.addEventListener("input", (event) => this._input(event));
    this.shadowRoot.addEventListener("change", (event) => this._change(event));
  }

  set hass(value) {
    this._hass = value;
    if (!this._data && !this._busy) void this._load();
  }

  set panel(value) {
    this._panel = value;
  }

  connectedCallback() {
    this._render();
    this._timer ??= setInterval(() => {
      if (document.visibilityState === "visible" && !this._editor && !this._sender) {
        void this._load(true);
      }
    }, 15000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    this._stopCapture();
    for (const timer of this._feedbackTimers.values()) clearTimeout(timer);
    this._feedbackTimers.clear();
    if (this._editor?.draftId) void this._cancelDraft(this._editor.draftId);
  }

  async _ws(type, data = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `http_data_bridge/${type}`,
      ...data,
    });
  }

  _err(error) {
    return error instanceof Error ? error.message : String(error?.message ?? error);
  }

  async _load(silent = false) {
    if (!this._hass || this._busy) return;
    this._busy = true;
    if (!silent) this._render();
    try {
      this._data = await this._ws("sources");
      this._error = "";
    } catch (error) {
      this._error = this._err(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _source(id) {
    return this._data?.sources?.find((source) => source.source_id === id);
  }

  _newBuilder(url = "") {
    this._builderCounter += 1;
    return {
      id: `builder-${this._builderCounter}`,
      url,
      open: false,
      mode: "fields",
      tab: "powershell",
      rows: [
        {
          id: crypto.randomUUID?.() ?? `${Date.now()}-${this._builderCounter}`,
          name: "",
          type: "text",
          value: "",
        },
      ],
      json: '{\n  "example": "value"\n}',
      generated: false,
      generatedValue: null,
      generationError: "",
    };
  }

  _invalidateBuilder(builder) {
    if (!builder) return;
    builder.generated = false;
    builder.generatedValue = null;
    builder.generationError = "";
  }

  _newEditor(source = null) {
    const existing = (source?.fields || []).map((field) => ({
      path: field.path,
      name: field.name,
      platform: field.platform,
      store_in_attribute: !!field.attribute_backed,
      unit: field.unit || "",
    }));
    return {
      mode: source ? "edit" : "add",
      sourceId: source?.source_id || null,
      step: "settings",
      name: source?.name || "Push source",
      enabled: source?.enabled ?? true,
      localOnly: source?.local_only ?? false,
      staleAfter: source?.stale_after ?? 0,
      method: source ? "keep" : "live",
      sample: '{\n  "temperature": 21.4,\n  "online": true\n}',
      draftId: null,
      captureUrl: null,
      nodes: [],
      existing,
      mappings: new Map(existing.map((field) => [field.path, { ...field }])),
      builder: this._newBuilder(),
    };
  }

  _activeBuilder(element = null) {
    const builderId = element?.dataset?.builder;
    const candidates = [this._editor?.builder, this._sender?.builder].filter(Boolean);
    if (builderId) return candidates.find((builder) => builder.id === builderId) || null;
    return this._sender?.builder || this._editor?.builder || null;
  }

  _input(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;

    const builder = this._activeBuilder(target);
    const rowIndex = target.dataset.builderIndex;
    const builderField = target.dataset.builderField;
    if (builder && rowIndex !== undefined && builderField) {
      const row = builder.rows[Number(rowIndex)];
      if (row) row[builderField] = target.value;
      this._invalidateBuilder(builder);
      return;
    }
    if (builder && target.dataset.builderJson !== undefined) {
      builder.json = target.value;
      this._invalidateBuilder(builder);
      return;
    }

    if (!this._editor) return;
    const key = target.dataset.key;
    if (key === "name") this._editor.name = target.value;
    if (key === "stale") this._editor.staleAfter = target.value;
    if (key === "sample") this._editor.sample = target.value;

    const path = target.dataset.path;
    const field = target.dataset.field;
    if (path !== undefined && field) {
      const mapping = this._editor.mappings.get(path);
      if (mapping) mapping[field] = target.value;
    }
  }

  _change(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return;

    const builder = this._activeBuilder(target);
    const rowIndex = target.dataset.builderIndex;
    const builderField = target.dataset.builderField;
    if (builder && rowIndex !== undefined && builderField) {
      const row = builder.rows[Number(rowIndex)];
      if (row) {
        row[builderField] = target.value;
        if (builderField === "type" && row.type === "boolean" && !["true", "false"].includes(row.value)) {
          row.value = "true";
        }
      }
      this._invalidateBuilder(builder);
      this._render();
      return;
    }
    if (builder && target.dataset.builderJson !== undefined) {
      builder.json = target.value;
      this._invalidateBuilder(builder);
      this._render();
      return;
    }

    if (!this._editor) return;
    const key = target.dataset.key;
    if (key === "enabled" && target instanceof HTMLInputElement) this._editor.enabled = target.checked;
    if (key === "local" && target instanceof HTMLInputElement) this._editor.localOnly = target.checked;
    if (key === "method") this._editor.method = target.value;

    const path = target.dataset.path;
    const field = target.dataset.field;
    if (path === undefined || !field) return;
    const node = this._editor.nodes.find((item) => item.path === path);

    if (field === "selected" && target instanceof HTMLInputElement) {
      if (target.checked && !this._editor.mappings.has(path)) {
        this._editor.mappings.set(path, this._defaultMap(node));
        this._expandAncestors(path);
      }
      if (!target.checked) this._editor.mappings.delete(path);
      this._render();
      return;
    }

    const mapping = this._editor.mappings.get(path);
    if (!mapping) return;
    if (field === "platform") {
      mapping.platform = target.value;
      if (mapping.platform === "binary_sensor") {
        mapping.store_in_attribute = false;
        mapping.unit = "";
      }
      this._render();
    }
    if (field === "store" && target instanceof HTMLInputElement) {
      mapping.store_in_attribute = target.checked;
      if (mapping.store_in_attribute) mapping.unit = "";
      this._render();
    }
  }

  _defaultMap(node) {
    const old = this._editor.existing.find((field) => field.path === node.path);
    if (old) {
      return {
        ...old,
        platform: old.platform === "binary_sensor" && !node.is_boolean ? "sensor" : old.platform,
        store_in_attribute: node.requires_attribute || old.store_in_attribute,
        unit: node.requires_attribute || old.store_in_attribute ? "" : old.unit,
      };
    }
    const structured = node.kind === "object" || node.kind === "array";
    return {
      path: node.path,
      name: node.path === "" ? "Payload" : node.suggested_name,
      platform: structured ? "sensor" : node.is_boolean ? "binary_sensor" : "sensor",
      store_in_attribute: structured || !!node.requires_attribute,
      unit: "",
    };
  }

  _flash(key, text, duration = 1800) {
    const token = Symbol(key);
    this._feedback.set(key, { text, token });
    const oldTimer = this._feedbackTimers.get(key);
    if (oldTimer) clearTimeout(oldTimer);
    const timer = setTimeout(() => {
      if (this._feedback.get(key)?.token === token) {
        this._feedback.delete(key);
        this._feedbackTimers.delete(key);
        this._render();
      }
    }, duration);
    this._feedbackTimers.set(key, timer);
    this._render();
  }

  _feedbackText(key, fallback) {
    return this._feedback.get(key)?.text || fallback;
  }

  async _click(event) {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    if (!button) return;
    const action = button.dataset.action;
    const id = button.dataset.id;

    if (action === "refresh") return void await this._load();
    if (action === "settings") {
      history.pushState(null, "", "/config/integrations/integration/http_data_bridge");
      dispatchEvent(new Event("location-changed"));
      return;
    }
    if (action === "add") {
      this._editor = this._newEditor();
      this._expanded.clear();
      this._error = "";
      this._render();
      return;
    }
    if (action === "edit") {
      this._editor = this._newEditor(this._source(id));
      this._expanded.clear();
      this._error = "";
      this._render();
      return;
    }
    if (action === "delete") {
      this._delete = this._source(id);
      this._render();
      return;
    }
    if (action === "delete-cancel") {
      this._delete = null;
      this._render();
      return;
    }
    if (action === "delete-go") return void await this._deleteSource();
    if (action === "reveal") {
      this._revealed.has(id) ? this._revealed.delete(id) : this._revealed.add(id);
      this._render();
      return;
    }
    if (action === "copy") {
      return void await this._copy(
        this._source(id)?.webhook_url || "",
        `source-copy:${id}`,
      );
    }
    if (action === "sender") {
      const source = this._source(id);
      if (!source) return;
      const builder = this._newBuilder(source.webhook_url);
      builder.open = true;
      this._sender = { source, builder };
      this._render();
      return;
    }
    if (action === "sender-close") {
      this._sender = null;
      this._render();
      return;
    }
    if (action === "view") return void await this._viewValue(this._source(id), button.dataset.path ?? "");
    if (action === "view-close") {
      this._view = null;
      this._render();
      return;
    }
    if (action === "editor-cancel") return void await this._closeEditor();
    if (action === "back") return void await this._back();
    if (action === "next") return void await this._next();
    if (action === "analyze") return void await this._prepareSample();
    if (action === "capture-copy") {
      return void await this._copy(this._editor?.captureUrl || "", "capture-copy");
    }
    if (action === "capture-check") return void await this._pollCapture(false, true);
    if (action === "save") return void await this._save();
    if (action === "toggle-node") {
      const path = button.dataset.path ?? "";
      this._expanded.has(path) ? this._expanded.delete(path) : this._expanded.add(path);
      this._render();
      return;
    }
    if (action === "select-whole") {
      const path = button.dataset.path ?? "";
      const node = this._editor?.nodes.find((item) => item.path === path);
      if (!node) return;
      if (this._editor.mappings.has(path)) {
        this._editor.mappings.delete(path);
      } else {
        const mapping = this._defaultMap(node);
        mapping.platform = "sensor";
        mapping.store_in_attribute = true;
        mapping.unit = "";
        this._editor.mappings.set(path, mapping);
        this._expandAncestors(path);
      }
      this._render();
      return;
    }
    if (action === "technical") {
      this._technical = !this._technical;
      this._render();
      return;
    }

    const builder = this._activeBuilder(button);
    if (action === "builder-toggle" && builder) {
      builder.open = !builder.open;
      this._render();
      return;
    }
    if (action === "builder-add" && builder) {
      builder.rows.push({
        id: crypto.randomUUID?.() ?? `${Date.now()}-${builder.rows.length}`,
        name: "",
        type: "text",
        value: "",
      });
      this._invalidateBuilder(builder);
      this._render();
      return;
    }
    if (action === "builder-remove" && builder) {
      const index = Number(button.dataset.builderIndex);
      builder.rows.splice(index, 1);
      if (!builder.rows.length) {
        builder.rows.push({ id: `${Date.now()}-0`, name: "", type: "text", value: "" });
      }
      this._invalidateBuilder(builder);
      this._render();
      return;
    }
    if (action === "builder-mode" && builder) {
      const mode = button.dataset.mode;
      if (mode === "json" && builder.mode !== "json") {
        const parsed = this._builderPayload(builder);
        if (!parsed.error) builder.json = JSON.stringify(parsed.value, null, 2);
      }
      builder.mode = mode === "json" ? "json" : "fields";
      this._invalidateBuilder(builder);
      this._render();
      return;
    }
    if (action === "builder-generate" && builder) {
      this._generateBuilder(builder);
      return;
    }
    if (action === "builder-tab" && builder) {
      builder.tab = button.dataset.tab || "powershell";
      this._render();
      return;
    }
    if (action === "builder-copy" && builder) {
      const result = this._builderCode(builder);
      if (result.error) {
        this._flash(`builder-copy:${builder.id}`, "Generate again first");
        return;
      }
      return void await this._copy(
        result.code,
        `builder-copy:${builder.id}`,
        "✓ Copied",
      );
    }
  }

  _validateSettings() {
    if (!this._editor.name.trim()) return "Enter a source name.";
    const timeout = Number(this._editor.staleAfter);
    if (!Number.isInteger(timeout) || timeout < 0) {
      return "Stale timeout must be a non-negative whole number.";
    }
    return "";
  }

  async _next() {
    const error = this._validateSettings();
    if (error) {
      this._error = error;
      this._render();
      return;
    }
    this._error = "";
    if (this._editor.mode === "edit" && this._editor.method === "keep") {
      return void await this._save();
    }
    if (this._editor.method === "paste") {
      this._editor.step = "sample";
      this._render();
      return;
    }
    await this._startCapture();
  }

  async _back() {
    if (this._editor.step === "mappings") {
      this._editor.step = this._editor.method === "paste" ? "sample" : "capture";
      if (this._editor.step === "capture") this._startCapturePoll();
    } else {
      await this._cancelCurrentDraft();
      this._editor.step = "settings";
    }
    this._error = "";
    this._render();
  }

  async _prepareSample() {
    this._busy = true;
    this._render();
    try {
      await this._cancelCurrentDraft();
      const result = await this._ws("source/prepare_sample", {
        sample_payload: this._editor.sample,
      });
      this._editor.draftId = result.draft_id;
      this._editor.nodes = result.nodes;
      this._primeMappings();
      this._editor.step = "mappings";
      this._error = "";
    } catch (error) {
      this._error = this._err(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _startCapture() {
    this._busy = true;
    this._render();
    try {
      await this._cancelCurrentDraft();
      const result = await this._ws("source/capture/start", {
        source_name: this._editor.name,
        local_only: !!this._editor.localOnly,
      });
      this._editor.draftId = result.draft_id;
      this._editor.captureUrl = result.capture_url;
      this._editor.builder.url = result.capture_url;
      this._editor.step = "capture";
      this._error = "";
      this._startCapturePoll();
    } catch (error) {
      this._error = this._err(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _startCapturePoll() {
    this._stopCapture();
    this._captureTimer = setInterval(() => void this._pollCapture(true, false), 1800);
  }

  _stopCapture() {
    if (this._captureTimer) {
      clearInterval(this._captureTimer);
      this._captureTimer = null;
    }
  }

  async _pollCapture(silent = false, manual = false) {
    if (!this._editor?.draftId) return;
    if (manual) this._flash("capture-check", "Checking…", 5000);
    try {
      const result = await this._ws("source/capture/status", {
        draft_id: this._editor.draftId,
      });
      if (result.captured) {
        this._stopCapture();
        this._feedback.delete("capture-check");
        this._editor.nodes = result.nodes;
        this._primeMappings();
        this._editor.step = "mappings";
        this._error = "";
        this._render();
      } else if (manual) {
        this._flash("capture-check", "Still waiting", 1800);
      } else if (!silent) {
        this._render();
      }
    } catch (error) {
      this._stopCapture();
      this._feedback.delete("capture-check");
      this._error = this._err(error);
      this._render();
    }
  }

  _primeMappings() {
    const valid = new Set(this._editor.nodes.map((node) => node.path));
    for (const path of [...this._editor.mappings.keys()]) {
      if (!valid.has(path)) this._editor.mappings.delete(path);
    }
    for (const node of this._editor.nodes) {
      const old = this._editor.existing.find((field) => field.path === node.path);
      if (old && !this._editor.mappings.has(node.path)) {
        this._editor.mappings.set(node.path, this._defaultMap(node));
      }
    }
    for (const path of this._editor.mappings.keys()) this._expandAncestors(path);
  }

  _mappingError() {
    if (!this._editor.mappings.size) return "Choose at least one value to add to Home Assistant.";
    for (const mapping of this._editor.mappings.values()) {
      if (!mapping.name.trim()) return "Every selected value needs an entity name.";
      const node = this._editor.nodes.find((item) => item.path === mapping.path);
      const label = this._friendlyName(node);
      if (mapping.platform === "binary_sensor" && !node?.is_boolean) {
        return `${label} can only be a binary sensor when the example is Yes/No.`;
      }
      if (node?.requires_attribute && !mapping.store_in_attribute) {
        return `${label} must use attribute storage because it cannot fit in a normal sensor state.`;
      }
      if (mapping.store_in_attribute && mapping.unit) {
        return "Attribute-backed sensors cannot have a unit of measurement.";
      }
      if (mapping.unit && !node?.is_number) return "Units require numeric example values.";
    }
    return "";
  }

  async _save() {
    if (this._busy) return;
    const keep = this._editor.mode === "edit" && this._editor.method === "keep";
    if (!keep) {
      const error = this._mappingError();
      if (error) {
        this._error = error;
        this._render();
        return;
      }
    }

    this._busy = true;
    this._render();
    try {
      const data = {
        name: this._editor.name.trim(),
        enabled: !!this._editor.enabled,
        local_only: !!this._editor.localOnly,
        stale_after: Number(this._editor.staleAfter),
        replace_mappings: !keep,
        fields: keep
          ? []
          : [...this._editor.mappings.values()].map((mapping) => ({
              path: mapping.path,
              name: mapping.name.trim(),
              platform: mapping.platform,
              ...(mapping.store_in_attribute ? { store_in_attribute: true } : {}),
              ...(!mapping.store_in_attribute && mapping.unit.trim()
                ? { unit: mapping.unit.trim() }
                : {}),
            })),
      };
      if (this._editor.sourceId) data.source_id = this._editor.sourceId;
      if (!keep) data.draft_id = this._editor.draftId;
      await this._ws("source/save", data);
      this._editor.draftId = null;
      this._stopCapture();
      this._editor = null;
      this._error = "";
      await this._load(true);
    } catch (error) {
      this._error = this._err(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _deleteSource() {
    if (!this._delete || this._busy) return;
    this._busy = true;
    this._render();
    try {
      await this._ws("source/delete", { source_id: this._delete.source_id });
      this._delete = null;
      this._error = "";
      await this._load(true);
    } catch (error) {
      this._error = this._err(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _closeEditor() {
    await this._cancelCurrentDraft();
    this._editor = null;
    this._error = "";
    this._render();
  }

  async _cancelCurrentDraft() {
    this._stopCapture();
    const id = this._editor?.draftId;
    if (id) {
      this._editor.draftId = null;
      try {
        await this._cancelDraft(id);
      } catch (_error) {
      }
    }
  }

  async _cancelDraft(id) {
    return this._ws("source/draft/cancel", { draft_id: id });
  }

  async _viewValue(source, path) {
    const field = source?.fields.find((item) => item.path === path);
    if (!field) return;
    this._view = {
      title: field.name,
      source: source.name,
      path,
      loading: true,
      value: null,
      available: false,
      error: "",
    };
    this._render();
    try {
      const result = await this._ws("attribute_value", {
        source_id: source.source_id,
        path,
      });
      Object.assign(this._view, {
        loading: false,
        value: result.value,
        available: !!result.available,
      });
    } catch (error) {
      Object.assign(this._view, { loading: false, error: this._err(error) });
    }
    this._render();
  }

  async _copy(text, feedbackKey, successText = "✓ Copied") {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_error) {
      try {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        document.body.append(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      } catch (_fallbackError) {
        this._flash(feedbackKey, "Copy failed");
        return;
      }
    }
    this._flash(feedbackKey, successText);
  }

  _nodeMap() {
    return new Map((this._editor?.nodes || []).map((node) => [node.path, node]));
  }

  _parentPath(path) {
    if (!path) return null;
    const index = path.lastIndexOf("/");
    return index <= 0 ? "" : path.slice(0, index);
  }

  _children(path) {
    return (this._editor?.nodes || []).filter(
      (node) => node.path !== "" && this._parentPath(node.path) === path,
    );
  }

  _decodePointerSegment(segment) {
    return segment.replaceAll("~1", "/").replaceAll("~0", "~");
  }

  _friendlyKey(value) {
    const spaced = String(value).replace(/[_-]+/g, " ").trim();
    if (!spaced) return "Value";
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }

  _friendlyName(node) {
    if (!node || node.path === "") return "Entire request";
    const rawSegment = node.path.slice(node.path.lastIndexOf("/") + 1);
    const segment = this._decodePointerSegment(rawSegment);
    const parent = this._nodeMap().get(this._parentPath(node.path));
    if (parent?.kind === "array" && /^\d+$/.test(segment)) {
      return `Item ${Number(segment) + 1}`;
    }
    return this._friendlyKey(segment);
  }

  _friendlyPreview(node) {
    if (!node) return "";
    if (node.kind === "object") {
      const count = this._children(node.path).length;
      return `Group containing ${count} ${count === 1 ? "value" : "values"}`;
    }
    if (node.kind === "array") {
      const count = this._children(node.path).length;
      return `List containing ${count} ${count === 1 ? "item" : "items"}`;
    }
    if (node.kind === "boolean") return node.preview === "true" ? "Yes" : "No";
    if (node.kind === "null") return "Empty value";
    return node.preview;
  }

  _technicalType(node) {
    return ({
      object: "JSON object",
      array: "JSON array",
      boolean: "Boolean",
      number: "Number",
      string: "Text",
      null: "Null",
    })[node?.kind] || node?.kind || "Value";
  }

  _expandAncestors(path) {
    let parent = this._parentPath(path);
    while (parent !== null) {
      if (parent !== "") this._expanded.add(parent);
      parent = this._parentPath(parent);
    }
  }

  _esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _time(value) {
    if (!value) return "Never";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? this._esc(value) : this._esc(date.toLocaleString());
  }

  _status(status) {
    return ({
      available: "Receiving data",
      stale: "Stale",
      waiting: "Waiting for first payload",
      disabled: "Disabled",
      unloaded: "Not loaded",
    })[status] || status;
  }

  _card(source) {
    const fields = source.fields
      .map((field) => {
        const current = !field.available
          ? '<span class="muted">Unavailable</span>'
          : field.attribute_backed
            ? `<button data-action="view" data-id="${this._esc(source.source_id)}" data-path="${this._esc(field.path)}">View value</button>`
            : this._esc(typeof field.value === "string" ? field.value : JSON.stringify(field.value));
        const type = field.attribute_backed
          ? "Sensor · value stored in attribute"
          : field.platform === "binary_sensor"
            ? "Binary sensor"
            : `Sensor${field.unit ? ` · ${this._esc(field.unit)}` : ""}`;
        return `<tr>
          <td><b>${this._esc(field.name)}</b><small>${this._esc(field.path_label)}</small></td>
          <td>${type}</td>
          <td>${current}</td>
        </tr>`;
      })
      .join("");
    const shown = this._revealed.has(source.source_id);
    const copyKey = `source-copy:${source.source_id}`;
    return `<section class="card">
      <div class="row card-title">
        <div>
          <h2>${this._esc(source.name)}</h2>
          <small>${source.local_only ? "Local network only" : source.uses_cloudhook ? "Nabu Casa cloudhook" : "Home Assistant webhook URL"}</small>
        </div>
        <div class="actions">
          <span class="status ${this._esc(source.status)}">${this._esc(this._status(source.status))}</span>
          <button data-action="edit" data-id="${this._esc(source.source_id)}">Edit</button>
          <button class="dangerText" data-action="delete" data-id="${this._esc(source.source_id)}">Delete</button>
        </div>
      </div>
      <div class="stats">
        <div><small>Last received</small><b>${this._time(source.last_received)}</b></div>
        <div><small>Stale timeout</small><b>${source.stale_after ? `${source.stale_after}s` : "Never"}</b></div>
        <div><small>Home Assistant entities</small><b>${source.fields.length}</b></div>
      </div>
      <label>Webhook URL</label>
      <div class="url">
        <input readonly type="${shown ? "text" : "password"}" value="${this._esc(source.webhook_url)}">
        <button data-action="reveal" data-id="${this._esc(source.source_id)}">${shown ? "Hide" : "Show"}</button>
        <button data-action="copy" data-id="${this._esc(source.source_id)}">${this._esc(this._feedbackText(copyKey, "Copy"))}</button>
      </div>
      <div class="row secondary-row">
        <small>Treat this URL like a password.</small>
        <button data-action="sender" data-id="${this._esc(source.source_id)}">Sender examples</button>
      </div>
      <div class="table"><table>
        <thead><tr><th>Entity</th><th>Type</th><th>Current</th></tr></thead>
        <tbody>${fields}</tbody>
      </table></div>
    </section>`;
  }

  _settingsStep() {
    const editor = this._editor;
    return `<div class="form">
      <label>Source name
        <input data-key="name" value="${this._esc(editor.name)}">
      </label>
      <label>Mark unavailable after (seconds)
        <input data-key="stale" type="number" min="0" step="1" value="${this._esc(editor.staleAfter)}">
        <small>0 keeps the latest value available indefinitely.</small>
      </label>
      <label class="check"><input data-key="enabled" type="checkbox" ${editor.enabled ? "checked" : ""}>Enable source</label>
      <label class="check"><input data-key="local" type="checkbox" ${editor.localOnly ? "checked" : ""}>Only allow local network requests</label>
      <h3>${editor.mode === "edit" ? "Entity mappings" : "How should we learn what this source sends?"}</h3>
      ${editor.mode === "edit" ? `<label class="choice"><input data-key="method" type="radio" name="method" value="keep" ${editor.method === "keep" ? "checked" : ""}><span><b>Keep current mappings</b><small>Change settings without changing your Home Assistant entities.</small></span></label>` : ""}
      <label class="choice"><input data-key="method" type="radio" name="method" value="live" ${editor.method === "live" ? "checked" : ""}><span><b>Send an example request</b><small>We'll give you a temporary webhook URL and detect the JSON you send.</small></span></label>
      <label class="choice"><input data-key="method" type="radio" name="method" value="paste" ${editor.method === "paste" ? "checked" : ""}><span><b>Paste example JSON</b><small>Useful if you already have a sample payload.</small></span></label>
    </div>`;
  }

  _sampleStep() {
    return `<div class="form">
      <h3>Paste example JSON</h3>
      <p class="lead">Paste one example of the data your app or service will send. You'll choose what becomes Home Assistant entities on the next screen.</p>
      <textarea class="sample" data-key="sample" spellcheck="false">${this._esc(this._editor.sample)}</textarea>
      <small>The example is used only for setup and is not kept as a raw payload.</small>
    </div>`;
  }

  _captureStep() {
    const builder = this._editor.builder;
    const copyLabel = this._feedbackText("capture-copy", "Copy URL");
    const checkLabel = this._feedbackText("capture-check", "Check now");
    return `<div class="form">
      <h3>Send an example request</h3>
      <p class="lead">Send JSON from your app, script, or service to this temporary URL. We'll detect it automatically.</p>
      <div class="capture-status"><span class="pulse"></span><div><b>Waiting for a request…</b><small>This setup URL is temporary and will be removed when you finish or cancel.</small></div></div>
      <label>Temporary webhook URL</label>
      <div class="url">
        <input readonly value="${this._esc(this._editor.captureUrl || "")}">
        <button data-action="capture-copy">${this._esc(copyLabel)}</button>
      </div>
      <div class="capture-actions">
        <button data-action="capture-check">${this._esc(checkLabel)}</button>
        <small>Automatic checking is already active.</small>
      </div>
      ${this._builderHtml(builder, { collapsible: true, title: "Need help sending a request?" })}
    </div>`;
  }

  _leafCount() {
    return (this._editor?.nodes || []).filter(
      (node) => node.path !== "" && node.kind !== "object" && node.kind !== "array",
    ).length;
  }

  _containerCount() {
    return (this._editor?.nodes || []).filter(
      (node) => node.path !== "" && (node.kind === "object" || node.kind === "array"),
    ).length;
  }

  _mappingEditor(node, mapping) {
    const structured = node.kind === "object" || node.kind === "array";
    const friendly = this._friendlyName(node);
    if (structured || node.path === "") {
      return `<div class="mapping-editor structured-editor">
        <label>Home Assistant entity name
          <input data-path="${this._esc(node.path)}" data-field="name" value="${this._esc(mapping.name)}">
        </label>
        <div class="info-box"><b>Creates a Sensor</b><span>The selected ${node.path === "" ? "request" : node.kind === "array" ? "list" : "group"} is stored in the sensor's <code>value</code> attribute. The sensor state shows when the data was last received.</span></div>
        ${this._technical ? `<small>Technical path: <code>${this._esc(node.label)}</code> · ${this._esc(this._technicalType(node))}</small>` : ""}
      </div>`;
    }

    const sensor = mapping.platform === "sensor";
    const requiredAttribute = !!node.requires_attribute;
    const canHaveUnit = sensor && node.is_number && !mapping.store_in_attribute;
    return `<div class="mapping-editor">
      <label>Home Assistant entity name
        <input data-path="${this._esc(node.path)}" data-field="name" value="${this._esc(mapping.name)}">
      </label>
      <label>Entity type
        <select data-path="${this._esc(node.path)}" data-field="platform">
          <option value="sensor" ${sensor ? "selected" : ""}>Sensor</option>
          ${node.is_boolean ? `<option value="binary_sensor" ${mapping.platform === "binary_sensor" ? "selected" : ""}>Binary sensor</option>` : ""}
        </select>
        <small>${mapping.platform === "binary_sensor" ? "Binary sensors are designed for simple Yes/No or On/Off values." : "Sensors hold a value such as text, a number, or a timestamp."}</small>
      </label>
      ${sensor ? `<label class="check attribute-option"><input data-path="${this._esc(node.path)}" data-field="store" type="checkbox" ${mapping.store_in_attribute ? "checked" : ""} ${requiredAttribute ? "disabled" : ""}><span><b>Store value in an attribute instead of the sensor state</b><small>Home Assistant sensor states are limited to 255 characters. Use this for long text or structured data.${requiredAttribute ? ` Required for this example (${this._esc(friendly)} cannot fit in a normal sensor state).` : ""}</small></span></label>` : ""}
      ${canHaveUnit ? `<label>Unit of measurement <input data-path="${this._esc(node.path)}" data-field="unit" value="${this._esc(mapping.unit || "")}" placeholder="e.g. °C, %, W"><small>Optional. Use only if this number has a real unit.</small></label>` : ""}
      ${this._technical ? `<small>Technical path: <code>${this._esc(node.label)}</code> · ${this._esc(this._technicalType(node))}</small>` : ""}
    </div>`;
  }

  _nodeHtml(node, depth = 0) {
    const mapping = this._editor.mappings.get(node.path);
    const selected = !!mapping;
    const structured = node.kind === "object" || node.kind === "array";
    const name = this._friendlyName(node);
    const preview = this._friendlyPreview(node);
    const technical = this._technical
      ? `<small class="technical">Path: <code>${this._esc(node.label)}</code> · ${this._esc(this._technicalType(node))}</small>`
      : "";

    if (structured) {
      const expanded = this._expanded.has(node.path);
      const noun = node.kind === "array" ? "list" : "group";
      const children = expanded
        ? this._children(node.path).map((child) => this._nodeHtml(child, depth + 1)).join("")
        : "";
      return `<div class="tree-node container-node depth-${Math.min(depth, 5)}">
        <div class="container-head">
          <button class="expand" data-action="toggle-node" data-path="${this._esc(node.path)}" aria-expanded="${expanded}">${expanded ? "▾" : "▸"}</button>
          <button class="container-title" data-action="toggle-node" data-path="${this._esc(node.path)}">
            <span><b>${this._esc(name)}</b><small>${this._esc(preview)}</small>${technical}</span>
          </button>
          <button class="secondary ${selected ? "selected" : ""}" data-action="select-whole" data-path="${this._esc(node.path)}">${selected ? `Remove whole ${noun} sensor` : `Use entire ${noun} as one sensor`}</button>
        </div>
        ${selected ? this._mappingEditor(node, mapping) : ""}
        ${expanded ? `<div class="children">${children || '<div class="empty-child">This group is empty.</div>'}</div>` : ""}
      </div>`;
    }

    return `<div class="tree-node leaf-node depth-${Math.min(depth, 5)} ${selected ? "selected-node" : ""}">
      <label class="leaf-select">
        <input type="checkbox" data-path="${this._esc(node.path)}" data-field="selected" ${selected ? "checked" : ""}>
        <span><b>${this._esc(name)}</b><small>Example: ${this._esc(preview)}</small>${technical}</span>
      </label>
      ${selected ? this._mappingEditor(node, mapping) : ""}
    </div>`;
  }

  _advancedRootHtml() {
    const root = this._editor.nodes.find((node) => node.path === "");
    if (!root) return "";
    const selected = this._editor.mappings.has("");
    const mapping = this._editor.mappings.get("");
    return `<details class="advanced" ${selected ? "open" : ""}>
      <summary>Advanced options</summary>
      <div class="advanced-body">
        <p>Most people should choose individual values above. You can instead keep the complete JSON request as one sensor.</p>
        <button class="secondary ${selected ? "selected" : ""}" data-action="select-whole" data-path="">${selected ? "Remove entire-request sensor" : "Use the entire request as one sensor"}</button>
        ${selected ? this._mappingEditor(root, mapping) : ""}
        ${this._technical ? `<small>Technical JSON Pointer for the entire document: <code>$</code> (internally an empty pointer).</small>` : ""}
      </div>
    </details>`;
  }

  _mappingsStep() {
    const topLevel = this._children("");
    const leafCount = this._leafCount();
    const containerCount = this._containerCount();
    const selectedCount = this._editor.mappings.size;
    return `<div class="mapping-step">
      <div class="mapping-intro">
        <div>
          <h3>Choose what you want in Home Assistant</h3>
          <p class="lead">Select individual values below. Groups and lists can also be kept whole when you need the complete structured data.</p>
          <small>Found ${leafCount} individual ${leafCount === 1 ? "value" : "values"}${containerCount ? ` and ${containerCount} ${containerCount === 1 ? "group/list" : "groups/lists"}` : ""}.</small>
        </div>
        <button class="secondary" data-action="technical">${this._technical ? "Hide technical details" : "Show technical details"}</button>
      </div>
      <div class="selection-summary"><b>${selectedCount}</b> ${selectedCount === 1 ? "Home Assistant entity" : "Home Assistant entities"} selected</div>
      <div class="tree">${topLevel.map((node) => this._nodeHtml(node, 0)).join("") || '<div class="empty">No values were found in this example.</div>'}</div>
      ${this._advancedRootHtml()}
    </div>`;
  }

  _builderPayload(builder) {
    if (builder.mode === "json") {
      try {
        return { value: JSON.parse(builder.json), error: "" };
      } catch (_error) {
        return { value: null, error: "Enter valid JSON to generate code." };
      }
    }

    const payload = {};
    if (!builder.rows.length) return { value: null, error: "Add at least one example value." };
    for (const row of builder.rows) {
      const name = row.name.trim();
      if (!name) return { value: null, error: "Give every example value a name." };
      if (Object.prototype.hasOwnProperty.call(payload, name)) {
        return { value: null, error: `The name ${name} is used more than once.` };
      }
      if (row.type === "number") {
        const number = Number(row.value);
        if (!Number.isFinite(number) || row.value.trim() === "") {
          return { value: null, error: `${name} needs a valid number.` };
        }
        payload[name] = number;
      } else if (row.type === "boolean") {
        payload[name] = row.value === "true";
      } else {
        payload[name] = row.value;
      }
    }
    return { value: payload, error: "" };
  }

  _generateBuilder(builder) {
    const payloadResult = this._builderPayload(builder);
    if (payloadResult.error) {
      builder.generated = false;
      builder.generatedValue = null;
      builder.generationError = payloadResult.error;
      this._render();
      return false;
    }
    if (!builder.url) {
      builder.generated = false;
      builder.generatedValue = null;
      builder.generationError = "No webhook URL is available yet.";
      this._render();
      return false;
    }
    builder.generated = true;
    builder.generatedValue = payloadResult.value;
    builder.generationError = "";
    this._render();
    return true;
  }

  _shellQuote(value) {
    return `'${String(value).replaceAll("'", `'"'"'`)}'`;
  }

  _builderCode(builder) {
    if (!builder.generated) return { code: "", error: "Generate the request first." };
    if (!builder.url) return { code: "", error: "No webhook URL is available yet." };
    const compact = JSON.stringify(builder.generatedValue);
    const pretty = JSON.stringify(builder.generatedValue, null, 2);
    const urlJson = JSON.stringify(builder.url);

    if (builder.tab === "curl") {
      return {
        error: "",
        code: `curl -X POST ${this._shellQuote(builder.url)} \\
  -H 'Content-Type: application/json' \\
  --data-raw ${this._shellQuote(compact)}`,
      };
    }
    if (builder.tab === "javascript") {
      return {
        error: "",
        code: `fetch(${urlJson}, {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify(${pretty.replaceAll("\n", "\n  ")})\n});`,
      };
    }
    if (builder.tab === "php") {
      return {
        error: "",
        code: `$payload = <<<'JSON'\n${pretty}\nJSON;\n\n$ch = curl_init(${JSON.stringify(builder.url)});\ncurl_setopt_array($ch, [\n    CURLOPT_POST => true,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json'],\n    CURLOPT_POSTFIELDS => $payload,\n    CURLOPT_RETURNTRANSFER => true,\n]);\n$response = curl_exec($ch);\ncurl_close($ch);`,
      };
    }
    return {
      error: "",
      code: `$body = @'\n${pretty}\n'@\nInvoke-RestMethod -Method Post -Uri '${builder.url.replaceAll("'", "''")}' -ContentType 'application/json' -Body $body`,
    };
  }

  _builderRowsHtml(builder) {
    return builder.rows
      .map((row, index) => {
        const valueControl = row.type === "boolean"
          ? `<select data-builder="${builder.id}" data-builder-index="${index}" data-builder-field="value"><option value="true" ${row.value === "true" ? "selected" : ""}>Yes</option><option value="false" ${row.value === "false" ? "selected" : ""}>No</option></select>`
          : `<input data-builder="${builder.id}" data-builder-index="${index}" data-builder-field="value" ${row.type === "number" ? 'type="number" step="any"' : ""} value="${this._esc(row.value)}" placeholder="${row.type === "number" ? "e.g. 21.5" : "e.g. running"}">`;
        return `<div class="builder-row">
          <input data-builder="${builder.id}" data-builder-index="${index}" data-builder-field="name" value="${this._esc(row.name)}" placeholder="Name, e.g. temperature">
          <select data-builder="${builder.id}" data-builder-index="${index}" data-builder-field="type">
            <option value="text" ${row.type === "text" ? "selected" : ""}>Text</option>
            <option value="number" ${row.type === "number" ? "selected" : ""}>Number</option>
            <option value="boolean" ${row.type === "boolean" ? "selected" : ""}>Yes / No</option>
          </select>
          ${valueControl}
          <button class="icon-button" data-action="builder-remove" data-builder="${builder.id}" data-builder-index="${index}" title="Remove value">×</button>
        </div>`;
      })
      .join("");
  }

  _builderHtml(builder, { collapsible = false, title = "Build an example request" } = {}) {
    const copyKey = `builder-copy:${builder.id}`;
    const codeResult = builder.generated ? this._builderCode(builder) : { code: "", error: "" };
    const generated = builder.generated
      ? `<div class="request-preview"><small>JSON request preview</small><pre>${this._esc(JSON.stringify(builder.generatedValue, null, 2))}</pre></div>
        <h4>Ready-to-use code</h4>
        <div class="code-tabs">
          ${[["powershell", "PowerShell"], ["curl", "cURL"], ["javascript", "JavaScript"], ["php", "PHP"]].map(([tab, label]) => `<button data-action="builder-tab" data-builder="${builder.id}" data-tab="${tab}" class="${builder.tab === tab ? "active" : ""}">${label}</button>`).join("")}
        </div>
        ${codeResult.error ? `<div class="code-placeholder">${this._esc(codeResult.error)}</div>` : `<div class="code-box"><pre>${this._esc(codeResult.code)}</pre><button data-action="builder-copy" data-builder="${builder.id}">${this._esc(this._feedbackText(copyKey, "Copy code"))}</button></div>`}`
      : "";
    const content = `<div class="builder-content">
      <p>Add some example values your application might send. Nothing is validated until you choose <b>Generate</b>.</p>
      <div class="segmented">
        <button data-action="builder-mode" data-builder="${builder.id}" data-mode="fields" class="${builder.mode === "fields" ? "active" : ""}">Simple values</button>
        <button data-action="builder-mode" data-builder="${builder.id}" data-mode="json" class="${builder.mode === "json" ? "active" : ""}">Edit JSON directly</button>
      </div>
      ${builder.mode === "fields" ? `<div class="builder-labels"><span>Name</span><span>Type</span><span>Example value</span><span></span></div>${this._builderRowsHtml(builder)}<button class="secondary add-value" data-action="builder-add" data-builder="${builder.id}">+ Add value</button>` : `<textarea class="builder-json" data-builder="${builder.id}" data-builder-json spellcheck="false">${this._esc(builder.json)}</textarea>`}
      <div class="builder-generate"><button class="primary" data-action="builder-generate" data-builder="${builder.id}">${builder.generated ? "Generate again" : "Generate"}</button><small>We'll validate the example and show the code only after you generate it.</small></div>
      ${builder.generationError ? `<div class="inline-error">${this._esc(builder.generationError)}</div>` : ""}
      ${generated}
      <small>This is only a helper. HTTP Data Bridge does not require a predefined schema; you can send any valid JSON within the normal payload limit.</small>
    </div>`;

    if (!collapsible) return `<section class="builder"><h3>${this._esc(title)}</h3>${content}</section>`;
    return `<section class="builder ${builder.open ? "open" : ""}">
      <button class="builder-toggle" data-action="builder-toggle" data-builder="${builder.id}">
        <span><b>${this._esc(title)}</b><small>Build a test payload, then generate ready-to-copy PowerShell, cURL, JavaScript, or PHP code.</small></span><span>${builder.open ? "▴" : "▾"}</span>
      </button>
      ${builder.open ? content : ""}
    </section>`;
  }

  _editorFooter() {
    const step = this._editor.step;
    const left = step === "settings"
      ? `<button data-action="editor-cancel">Close</button>`
      : `<button data-action="back">Back</button>`;
    let right = "";
    if (step === "settings") right = `<button class="primary" data-action="next">${this._editor.mode === "edit" && this._editor.method === "keep" ? "Save changes" : "Continue"}</button>`;
    if (step === "sample") right = `<button class="primary" data-action="analyze">Continue</button>`;
    if (step === "capture") right = `<span class="muted">Waiting for your example request…</span>`;
    if (step === "mappings") right = `<button class="primary" data-action="save">Save source</button>`;
    return `<div class="modal-footer">${left}<div>${right}</div></div>`;
  }

  _editorModal() {
    if (!this._editor) return "";
    const body = this._editor.step === "settings"
      ? this._settingsStep()
      : this._editor.step === "sample"
        ? this._sampleStep()
        : this._editor.step === "capture"
          ? this._captureStep()
          : this._mappingsStep();
    const stepLabel = ({ settings: "Source settings", sample: "Example JSON", capture: "Send example", mappings: "Choose data" })[this._editor.step];
    return `<div class="overlay"><section class="modal large">
      <div class="modal-head"><div><h2>${this._editor.mode === "edit" ? "Edit source" : "Add source"}</h2><small>${this._esc(stepLabel)}</small></div><button data-action="editor-cancel">Close</button></div>
      ${this._error ? `<div class="error">${this._esc(this._error)}</div>` : ""}
      <div class="modal-body">${body}</div>
      ${this._editorFooter()}
    </section></div>`;
  }

  _senderModal() {
    if (!this._sender) return "";
    return `<div class="overlay"><section class="modal large">
      <div class="modal-head"><div><h2>Sender examples</h2><small>${this._esc(this._sender.source.name)}</small></div><button data-action="sender-close">Close</button></div>
      <div class="modal-body">
        <p class="lead">Use the permanent webhook URL below from your app, script, or service. Build an example payload if you want ready-to-copy code.</p>
        <div class="url"><input readonly value="${this._esc(this._sender.source.webhook_url)}"><button data-action="copy" data-id="${this._esc(this._sender.source.source_id)}">${this._esc(this._feedbackText(`source-copy:${this._sender.source.source_id}`, "Copy URL"))}</button></div>
        ${this._builderHtml(this._sender.builder, { title: "Build an example request" })}
      </div>
    </section></div>`;
  }

  _deleteModal() {
    if (!this._delete) return "";
    return `<div class="overlay"><section class="modal small">
      <div class="modal-head"><h2>Delete source?</h2></div>
      <div class="modal-body"><p>This will remove <b>${this._esc(this._delete.name)}</b>, its webhook, and its HTTP Data Bridge entities. This cannot be undone.</p></div>
      <div class="modal-footer"><button data-action="delete-cancel">Cancel</button><button class="danger" data-action="delete-go">Delete source</button></div>
    </section></div>`;
  }

  _viewModal() {
    if (!this._view) return "";
    let body = "";
    if (this._view.loading) body = `<p>Loading…</p>`;
    else if (this._view.error) body = `<div class="error">${this._esc(this._view.error)}</div>`;
    else if (!this._view.available) body = `<p>This value is currently unavailable.</p>`;
    else body = `<pre class="value-view">${this._esc(JSON.stringify(this._view.value, null, 2))}</pre>`;
    return `<div class="overlay"><section class="modal large">
      <div class="modal-head"><div><h2>${this._esc(this._view.title)}</h2><small>${this._esc(this._view.source)} · ${this._esc(this._view.path || "Entire request")}</small></div><button data-action="view-close">Close</button></div>
      <div class="modal-body">${body}</div>
    </section></div>`;
  }

  _styles() {
    return `<style>
      :host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui,-apple-system,sans-serif);--border:var(--divider-color,#ddd);--surface:var(--card-background-color,#fff);--muted:var(--secondary-text-color,#666);--accent:var(--primary-color,#03a9f4);--danger:#c62828}
      *{box-sizing:border-box}button,input,select,textarea{font:inherit;color:inherit}button{border:1px solid var(--border);background:var(--surface);border-radius:8px;padding:9px 13px;cursor:pointer}button:hover{background:var(--secondary-background-color,#f5f5f5)}button.primary{background:var(--accent);color:var(--text-primary-color,#fff);border-color:var(--accent);font-weight:600}button.secondary{padding:7px 10px}button.danger{background:var(--danger);border-color:var(--danger);color:#fff}button.dangerText{color:var(--danger)}button.selected{border-color:var(--accent);color:var(--accent)}button:disabled{opacity:.55;cursor:not-allowed}
      .page{max-width:1200px;margin:0 auto;padding:24px}.page-head,.row,.secondary-row,.mapping-intro{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-head{margin-bottom:20px}.page-head h1,.modal h2,.card h2{margin:0}.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.card{background:var(--surface);border-radius:12px;padding:20px;margin:16px 0;box-shadow:var(--ha-card-box-shadow,0 2px 4px rgba(0,0,0,.12))}.card-title{align-items:flex-start}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.stats>div{display:flex;flex-direction:column;gap:4px;padding:12px;background:var(--secondary-background-color,#f5f5f5);border-radius:8px}.status{padding:5px 8px;border-radius:999px;font-size:12px;background:var(--secondary-background-color,#eee)}.status.available{background:rgba(76,175,80,.14)}.status.stale{background:rgba(255,152,0,.16)}.status.disabled{opacity:.7}.url{display:flex;gap:8px;margin:6px 0}.url input{flex:1;min-width:0;font-family:monospace}.secondary-row{margin-top:6px}.table{overflow:auto;margin-top:18px}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:10px;border-bottom:1px solid var(--border);vertical-align:top}td small{display:block;color:var(--muted);margin-top:3px}.muted,small{color:var(--muted)}.empty{padding:32px;text-align:center;color:var(--muted)}
      input,select,textarea{border:1px solid var(--border);background:var(--surface);border-radius:8px;padding:10px;width:100%}textarea{resize:vertical}.form{display:grid;gap:16px}.form>label:not(.check):not(.choice),.mapping-editor>label:not(.check){display:grid;gap:6px;font-weight:600}.lead{margin-top:0;color:var(--muted);line-height:1.5}.check,.choice{display:flex;gap:10px;align-items:flex-start}.check input,.choice input{width:auto;margin-top:3px}.choice{padding:12px;border:1px solid var(--border);border-radius:10px}.choice span,.attribute-option span{display:grid;gap:3px}.sample,.builder-json{min-height:220px;font-family:monospace;line-height:1.45}
      .overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10;display:flex;align-items:center;justify-content:center;padding:20px}.modal{background:var(--surface);border-radius:14px;width:min(920px,100%);max-height:calc(100vh - 40px);display:flex;flex-direction:column;box-shadow:0 16px 50px rgba(0,0,0,.3)}.modal.large{width:min(1000px,100%)}.modal.small{width:min(480px,100%)}.modal-head,.modal-footer{padding:18px 20px;display:flex;align-items:center;justify-content:space-between;gap:16px}.modal-head{border-bottom:1px solid var(--border)}.modal-footer{border-top:1px solid var(--border)}.modal-body{padding:20px;overflow:auto}.error,.inline-error{padding:11px 14px;border-radius:8px;background:rgba(198,40,40,.1);color:var(--error-color,#c62828);margin:12px 20px}.inline-error{margin:12px 0}.value-view,pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
      .capture-status{display:flex;gap:12px;align-items:flex-start;padding:14px;border-radius:10px;background:rgba(3,169,244,.08)}.capture-status>div{display:grid;gap:3px}.pulse{width:11px;height:11px;border-radius:50%;background:var(--accent);margin-top:5px;box-shadow:0 0 0 0 rgba(3,169,244,.5);animation:pulse 1.8s infinite}@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(3,169,244,0)}100%{box-shadow:0 0 0 0 rgba(3,169,244,0)}}.capture-actions{display:flex;align-items:center;gap:10px}
      .mapping-step{display:grid;gap:14px}.mapping-intro{align-items:flex-start}.mapping-intro h3{margin:0 0 6px}.selection-summary{padding:10px 12px;background:var(--secondary-background-color,#f5f5f5);border-radius:8px}.tree{display:grid;gap:10px}.tree-node{border:1px solid var(--border);border-radius:10px;background:var(--surface);overflow:hidden}.leaf-select{display:flex;gap:10px;padding:13px 14px;align-items:flex-start}.leaf-select input{width:auto;margin-top:3px}.leaf-select span,.container-title span{display:grid;gap:3px}.selected-node{border-color:color-mix(in srgb,var(--accent) 55%,var(--border))}.container-head{display:flex;align-items:center;gap:6px;padding:8px}.expand{border:0;padding:7px;background:transparent;font-size:18px}.container-title{border:0;background:transparent;padding:5px;text-align:left;flex:1;display:flex}.children{display:grid;gap:8px;padding:0 10px 10px 32px}.children .tree-node{background:var(--secondary-background-color,#fafafa)}.empty-child{padding:12px;color:var(--muted)}.technical{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.mapping-editor{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:14px;border-top:1px solid var(--border);background:color-mix(in srgb,var(--accent) 4%,var(--surface))}.mapping-editor .check,.mapping-editor .info-box,.mapping-editor>small{grid-column:1/-1}.structured-editor{grid-template-columns:1fr}.info-box{display:grid;gap:4px;padding:11px 13px;background:var(--secondary-background-color,#f5f5f5);border-radius:8px}.advanced{border:1px dashed var(--border);border-radius:10px;padding:12px}.advanced summary{cursor:pointer;font-weight:600}.advanced-body{padding-top:12px;display:grid;gap:12px}.advanced-body p{margin:0;color:var(--muted)}
      .builder{margin-top:10px;border:1px solid var(--border);border-radius:10px;overflow:hidden}.builder-toggle{width:100%;border:0;border-radius:0;padding:14px;text-align:left;display:flex;align-items:center;justify-content:space-between}.builder-toggle span:first-child{display:grid;gap:3px}.builder-content{padding:16px;display:grid;gap:14px}.builder-content p{margin:0;color:var(--muted)}.segmented,.code-tabs{display:flex;gap:4px;flex-wrap:wrap}.segmented button,.code-tabs button{border-radius:999px;padding:7px 11px}.segmented button.active,.code-tabs button.active{background:var(--accent);color:#fff;border-color:var(--accent)}.builder-labels,.builder-row{display:grid;grid-template-columns:1.1fr .7fr 1.1fr 38px;gap:8px;align-items:center}.builder-labels{font-size:12px;color:var(--muted);padding:0 2px}.icon-button{padding:8px;font-size:18px}.add-value{justify-self:start}.builder-generate{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.request-preview{background:var(--secondary-background-color,#f5f5f5);border-radius:8px;padding:10px}.request-preview pre{margin:6px 0 0;max-height:220px;overflow:auto}.builder-content h4{margin:4px 0 0}.code-box{position:relative;background:#111;color:#f5f5f5;border-radius:9px;padding:14px}.code-box pre{margin:0;padding-right:100px;max-height:320px;overflow:auto}.code-box button{position:absolute;top:10px;right:10px;background:#222;color:#fff;border-color:#444}.code-placeholder{padding:16px;border:1px dashed var(--border);border-radius:8px;color:var(--muted)}
      @media(max-width:700px){.page{padding:14px}.page-head,.row,.mapping-intro,.secondary-row{align-items:stretch;flex-direction:column}.actions{width:100%}.stats{grid-template-columns:1fr}.url{display:grid;grid-template-columns:1fr auto}.url input{grid-column:1/-1}.modal{max-height:calc(100vh - 16px)}.overlay{padding:8px}.modal-body{padding:14px}.mapping-editor{grid-template-columns:1fr}.mapping-editor>*{grid-column:1}.container-head{align-items:flex-start;flex-wrap:wrap}.container-title{min-width:70%}.container-head>.secondary{margin-left:35px}.children{padding-left:14px}.builder-labels{display:none}.builder-row{grid-template-columns:1fr 1fr 38px}.builder-row>input:first-child{grid-column:1/-1}.builder-row>select{grid-column:1}.builder-row>input:not(:first-child),.builder-row>select+select{grid-column:2}.builder-row>.icon-button{grid-column:3;grid-row:2}.code-box pre{padding-right:0;padding-top:42px}}
    </style>`;
  }

  _render() {
    if (!this.shadowRoot) return;
    const sources = this._data?.sources || [];
    const cards = sources.length
      ? sources.map((source) => this._card(source)).join("")
      : `<div class="card empty"><h2>No push sources yet</h2><p>Add a source to turn incoming JSON into Home Assistant entities.</p><button class="primary" data-action="add">Add source</button></div>`;
    this.shadowRoot.innerHTML = `${this._styles()}
      <main class="page">
        <div class="page-head">
          <div><h1>HTTP Data Bridge</h1><small>Turn incoming JSON into native Home Assistant entities.</small></div>
          <div class="actions"><button data-action="refresh">${this._busy ? "Refreshing…" : "Refresh"}</button><button data-action="settings">HA settings</button><button class="primary" data-action="add">Add source</button></div>
        </div>
        ${this._error && !this._editor ? `<div class="error">${this._esc(this._error)}</div>` : ""}
        ${cards}
      </main>
      ${this._editorModal()}
      ${this._senderModal()}
      ${this._deleteModal()}
      ${this._viewModal()}`;
  }
}

customElements.define("http-data-bridge-panel", HttpDataBridgePanel);
