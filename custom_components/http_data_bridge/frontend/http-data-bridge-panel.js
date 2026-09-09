class HttpDataBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._data = null;
    this._loading = false;
    this._loaded = false;
    this._error = null;
    this._revealed = new Set();
    this._copyMessage = "";
    this._pollTimer = null;
    this._viewing = null;
    this._viewLoading = false;
    this._viewError = null;
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded && !this._loading) {
      void this._load();
    }
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    this._render();
    if (!this._pollTimer) {
      this._pollTimer = window.setInterval(() => {
        if (document.visibilityState === "visible") {
          void this._load(true);
        }
      }, 15000);
    }
  }

  disconnectedCallback() {
    if (this._pollTimer) {
      window.clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  async _load(silent = false) {
    if (!this._hass || this._loading) return;
    this._loading = true;
    if (!silent) this._render();

    try {
      this._data = await this._hass.connection.sendMessagePromise({
        type: "http_data_bridge/sources",
      });
      this._error = null;
      this._loaded = true;
    } catch (err) {
      this._error = err instanceof Error ? err.message : String(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _handleClick(event) {
    const target = event.target instanceof Element ? event.target.closest("button") : null;
    if (!target) return;

    const action = target.dataset.action;
    if (action === "refresh") {
      void this._load();
      return;
    }
    if (action === "manage") {
      window.history.pushState(null, "", "/config/integrations/integration/http_data_bridge");
      window.dispatchEvent(new Event("location-changed"));
      return;
    }
    if (action === "close-value") {
      this._viewing = null;
      this._viewError = null;
      this._render();
      return;
    }

    const sourceId = target.dataset.sourceId;
    if (!sourceId || !this._data) return;
    const source = this._data.sources.find((item) => item.source_id === sourceId);
    if (!source) return;

    if (action === "reveal") {
      if (this._revealed.has(sourceId)) this._revealed.delete(sourceId);
      else this._revealed.add(sourceId);
      this._render();
      return;
    }
    if (action === "copy") {
      void this._copy(source.webhook_url, source.name);
      return;
    }
    if (action === "view-value") {
      const path = target.dataset.path ?? "";
      const field = source.fields.find((item) => item.path === path);
      if (field) void this._viewAttributeValue(source, field);
    }
  }

  async _viewAttributeValue(source, field) {
    if (!this._hass || this._viewLoading) return;
    this._viewLoading = true;
    this._viewError = null;
    this._viewing = {
      sourceName: source.name,
      fieldName: field.name,
      path: field.path,
      available: false,
      value: null,
    };
    this._render();

    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "http_data_bridge/attribute_value",
        source_id: source.source_id,
        path: field.path,
      });
      this._viewing = {
        ...this._viewing,
        available: Boolean(result.available),
        value: result.value,
      };
    } catch (err) {
      this._viewError = err instanceof Error ? err.message : String(err);
    } finally {
      this._viewLoading = false;
      this._render();
    }
  }

  async _copy(text, sourceName) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_err) {
      const input = document.createElement("textarea");
      input.value = text;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.focus();
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    this._copyMessage = `Copied ${sourceName} webhook URL`;
    this._render();
    window.setTimeout(() => {
      if (this._copyMessage) {
        this._copyMessage = "";
        this._render();
      }
    }, 2500);
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _formatValue(value) {
    let text;
    if (value === null) text = "null";
    else if (typeof value === "string") text = value;
    else text = JSON.stringify(value);
    if (text.length > 160) text = `${text.slice(0, 159)}…`;
    return this._escape(text);
  }

  _formatFullValue(value) {
    if (typeof value === "string") return value;
    return JSON.stringify(value, null, 2);
  }

  _formatTime(value) {
    if (!value) return "Never";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? this._escape(value) : this._escape(date.toLocaleString());
  }

  _statusLabel(status) {
    return {
      available: "Receiving data",
      stale: "Stale",
      waiting: "Waiting for first payload",
      disabled: "Disabled",
      unloaded: "Not loaded",
    }[status] || status;
  }

  _reachability(source) {
    if (source.local_only) return "Local network only";
    if (source.uses_cloudhook) return "Nabu Casa cloudhook";
    return "Home Assistant webhook URL";
  }

  _sourceCard(source) {
    const revealed = this._revealed.has(source.source_id);
    const staleText = source.stale_after > 0 ? `${source.stale_after} seconds` : "Never expires";
    const fields = source.fields.length
      ? source.fields
          .map((field) => {
            const typeText = field.attribute_backed
              ? "sensor · attribute storage"
              : `${field.platform}${field.unit ? ` · ${this._escape(field.unit)}` : ""}`;
            let currentValue = "Unavailable";
            if (field.available && field.attribute_backed) {
              currentValue = `<button class="link-button" data-action="view-value" data-source-id="${this._escape(
                source.source_id
              )}" data-path="${this._escape(field.path)}">View value</button>`;
            } else if (field.available) {
              currentValue = this._formatValue(field.value);
            }

            return `
              <tr>
                <td>
                  <div class="field-name">${this._escape(field.name)}</div>
                  <div class="field-path">${this._escape(field.path_label)}</div>
                </td>
                <td>${typeText}</td>
                <td class="value ${field.available ? "" : "muted"}">${currentValue}</td>
              </tr>`;
          })
          .join("")
      : `<tr><td colspan="3" class="muted">No mapped fields</td></tr>`;

    return `
      <section class="card">
        <div class="card-head">
          <div>
            <h2>${this._escape(source.name)}</h2>
            <div class="meta">${this._reachability(source)}</div>
          </div>
          <span class="status status-${this._escape(source.status)}">${this._escape(
            this._statusLabel(source.status)
          )}</span>
        </div>

        <div class="stats">
          <div><span>Last received</span><strong>${this._formatTime(source.last_received)}</strong></div>
          <div><span>Stale timeout</span><strong>${this._escape(staleText)}</strong></div>
          <div><span>Mapped values</span><strong>${source.fields.length}</strong></div>
        </div>

        <div class="secret-block">
          <label>Webhook URL</label>
          <div class="secret-row">
            <input readonly type="${revealed ? "text" : "password"}" value="${this._escape(
              source.webhook_url
            )}" aria-label="${this._escape(source.name)} webhook URL">
            <button data-action="reveal" data-source-id="${this._escape(source.source_id)}">${
              revealed ? "Hide" : "Show"
            }</button>
            <button class="primary-small" data-action="copy" data-source-id="${this._escape(
              source.source_id
            )}">Copy</button>
          </div>
          <div class="warning">Treat this URL like a password. Anyone who knows it can submit data to this source.</div>
        </div>

        <div class="table-wrap">
          <table>
            <thead><tr><th>Value</th><th>Entity type</th><th>Current value</th></tr></thead>
            <tbody>${fields}</tbody>
          </table>
        </div>
      </section>`;
  }

  _valueDialog() {
    if (!this._viewing) return "";
    let body;
    if (this._viewLoading) {
      body = `<div class="modal-message">Loading value…</div>`;
    } else if (this._viewError) {
      body = `<div class="modal-message error">${this._escape(this._viewError)}</div>`;
    } else if (!this._viewing.available) {
      body = `<div class="modal-message muted">This value is currently unavailable.</div>`;
    } else {
      body = `<pre>${this._escape(this._formatFullValue(this._viewing.value))}</pre>`;
    }

    return `
      <div class="modal-backdrop">
        <section class="modal" role="dialog" aria-modal="true">
          <div class="modal-head">
            <div>
              <h2>${this._escape(this._viewing.fieldName)}</h2>
              <div class="meta">${this._escape(this._viewing.sourceName)} · ${this._escape(
                this._viewing.path || "$"
              )}</div>
            </div>
            <button data-action="close-value">Close</button>
          </div>
          ${body}
        </section>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot) return;
    const sources = this._data?.sources || [];
    const body = this._error
      ? `<div class="message error">Could not load HTTP Data Bridge: ${this._escape(this._error)}</div>`
      : !this._loaded
        ? `<div class="message">Loading sources…</div>`
        : sources.length === 0
          ? `<div class="message">No push sources are configured yet. Open integration settings to add one.</div>`
          : sources.map((source) => this._sourceCard(source)).join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); }
        * { box-sizing: border-box; }
        .page { max-width: 1120px; margin: 0 auto; padding: 24px; }
        header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
        h1 { margin: 0 0 6px; font-size: 28px; font-weight: 600; }
        .subtitle { color: var(--secondary-text-color); max-width: 700px; line-height: 1.45; }
        .actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
        button { border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); border-radius: 8px; padding: 9px 13px; cursor: pointer; font: inherit; }
        button:hover { background: var(--secondary-background-color); }
        .primary { background: var(--primary-color); color: var(--text-primary-color, white); border-color: var(--primary-color); }
        .primary-small { background: var(--primary-color); color: var(--text-primary-color, white); border-color: var(--primary-color); }
        .link-button { padding: 4px 8px; color: var(--primary-color); }
        .copy-message { margin: -8px 0 16px; color: var(--success-color, #43a047); font-size: 14px; }
        .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12)); padding: 20px; margin-bottom: 18px; }
        .card-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
        h2 { margin: 0 0 4px; font-size: 21px; }
        .meta, .muted { color: var(--secondary-text-color); }
        .status { font-size: 12px; font-weight: 600; border-radius: 999px; padding: 5px 9px; background: var(--secondary-background-color); white-space: nowrap; }
        .status-available { color: var(--success-color, #43a047); }
        .status-stale { color: var(--warning-color, #fb8c00); }
        .status-disabled, .status-unloaded { color: var(--secondary-text-color); }
        .stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }
        .stats div { background: var(--secondary-background-color); border-radius: 9px; padding: 12px; min-width: 0; }
        .stats span { display: block; color: var(--secondary-text-color); font-size: 12px; margin-bottom: 4px; }
        .stats strong { display: block; overflow-wrap: anywhere; font-size: 14px; }
        .secret-block { margin: 16px 0 20px; }
        .secret-block label { display: block; font-weight: 600; margin-bottom: 7px; }
        .secret-row { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; }
        input { min-width: 0; width: 100%; padding: 10px 11px; border: 1px solid var(--divider-color); border-radius: 8px; background: var(--primary-background-color); color: var(--primary-text-color); font: inherit; }
        .warning { color: var(--secondary-text-color); font-size: 12px; margin-top: 7px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th { text-align: left; color: var(--secondary-text-color); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--divider-color); }
        td { padding: 10px 8px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
        tbody tr:last-child td { border-bottom: 0; }
        .field-name { font-weight: 500; }
        .field-path { color: var(--secondary-text-color); font-family: monospace; font-size: 12px; margin-top: 2px; }
        .value { overflow-wrap: anywhere; max-width: 380px; }
        .message { background: var(--card-background-color); border-radius: 12px; padding: 24px; color: var(--secondary-text-color); }
        .error { color: var(--error-color); }
        .modal-backdrop { position: fixed; inset: 0; z-index: 20; background: rgba(0,0,0,.48); display: flex; align-items: center; justify-content: center; padding: 20px; }
        .modal { width: min(900px, 100%); max-height: 85vh; overflow: auto; background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); box-shadow: 0 8px 30px rgba(0,0,0,.35); padding: 20px; }
        .modal-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
        .modal pre { margin: 0; padding: 14px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--primary-background-color); border-radius: 8px; font-family: monospace; }
        .modal-message { padding: 18px 0; }
        @media (max-width: 700px) {
          .page { padding: 16px; }
          header { flex-direction: column; }
          .actions { justify-content: flex-start; }
          .stats { grid-template-columns: 1fr; }
          .secret-row { grid-template-columns: 1fr 1fr; }
          .secret-row input { grid-column: 1 / -1; }
          .card { padding: 16px; }
        }
      </style>
      <div class="page">
        <header>
          <div>
            <h1>HTTP Data Bridge</h1>
            <div class="subtitle">Push sources, selected values, freshness and webhook endpoints. Incoming payloads are not retained here beyond the values you explicitly mapped.</div>
          </div>
          <div class="actions">
            <button data-action="refresh" ${this._loading ? "disabled" : ""}>${this._loading ? "Refreshing…" : "Refresh"}</button>
            <button class="primary" data-action="manage">Manage sources</button>
          </div>
        </header>
        ${this._copyMessage ? `<div class="copy-message">${this._escape(this._copyMessage)}</div>` : ""}
        ${body}
      </div>
      ${this._valueDialog()}`;
  }
}

if (!customElements.get("http-data-bridge-panel")) {
  customElements.define("http-data-bridge-panel", HttpDataBridgePanel);
}
