/* ==========================================================================
   ACS · Control Deck — Core app: API client, helpers, UI toolkit, CrudPage.
   Offline, no CDN, vanilla JS. Loaded before page scripts.
   ========================================================================== */
"use strict";

/* ---------- DOM helpers ---------- */
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k === "value") n.value = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else n.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return n;
}

/* ---------- String / number / time helpers ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function pad(n) { return String(n).padStart(2, "0"); }
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${fmtTime(iso)}`;
}
function fmtNum(n, d = 2) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US", { maximumFractionDigits: d });
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/* ---------- API client ---------- */
function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}
async function request(method, url, body, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const init = { method, credentials: "same-origin", ...opts, headers };
  if (body !== undefined) {
    if (body instanceof FormData) { init.body = body; }
    else { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  }
  const csrf = getCookie("csrftoken");
  if (csrf) headers["X-CSRFToken"] = csrf;
  const res = await fetch(url, init);
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch (_) { data = text; } }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || (typeof data === "string" ? data : "") || res.statusText;
    throw new Error(msg || res.statusText);
  }
  return data;
}
const api = {
  get: (u, o) => request("GET", u, undefined, o),
  post: (u, d, o) => request("POST", u, d, o),
  put: (u, d, o) => request("PUT", u, d, o),
  patch: (u, d, o) => request("PATCH", u, d, o),
  del: (u, o) => request("DELETE", u, undefined, o),
};

/* ---------- Toast & modal ---------- */
function toast(msg, kind = "info") {
  const host = $("toast-host");
  if (!host) return;
  const t = el("div", { class: "toast " + (kind === "error" ? "err" : kind === "ok" ? "ok" : "") }, msg);
  host.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
function openModal(html) {
  const m = $("modal");
  $("modal-body").innerHTML = html;
  if (typeof m.showModal === "function") m.showModal();
  else m.setAttribute("open", "");
}
function closeModal() {
  const m = $("modal");
  if (typeof m.close === "function") m.close();
  else m.removeAttribute("open");
}
function confirmDialog(title, message, okLabel = "Delete") {
  return new Promise((resolve) => {
    openModal(`
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div style="padding-top:12px">
        <p style="color:var(--text-2)">${esc(message)}</p>
        <div class="modal-actions">
          <button class="btn btn-ghost" id="cf-no">Cancel</button>
          <button class="btn btn-danger" id="cf-yes">${esc(okLabel)}</button>
        </div>
      </div>`);
    $("cf-no").onclick = () => { closeModal(); resolve(false); };
    $("cf-yes").onclick = () => { closeModal(); resolve(true); };
  });
}

/* ---------- Status → label/color ---------- */
const STATUS = {
  order: {
    CREATED: { label: "Created", tone: "gray" }, ASSIGNED: { label: "Assigned", tone: "blue" },
    EXECUTING: { label: "Executing", tone: "teal" }, COMPLETED: { label: "Completed", tone: "green" },
    CANCELLED: { label: "Cancelled", tone: "gray" }, FAILED: { label: "Failed", tone: "red" },
  },
  robot: {
    INIT: { label: "Initializing", tone: "gray" }, IDLE: { label: "Idle", tone: "gray" },
    RUNNING: { label: "Running", tone: "blue" }, WARNING: { label: "Warning", tone: "amber" },
    BLOCKED: { label: "Blocked", tone: "amber" }, ERROR: { label: "Error", tone: "red" },
    OFFLINE: { label: "Offline", tone: "gray" },
  },
  master: {
    NEW: { label: "New", tone: "gray" }, PLANNING: { label: "Planning", tone: "blue" },
    PROCESSING: { label: "Processing", tone: "teal" }, COMPLETED: { label: "Completed", tone: "green" },
    FAILED: { label: "Failed", tone: "red" }, CANCELLED: { label: "Cancelled", tone: "gray" },
  },
  suborder: {
    PENDING: { label: "Pending", tone: "gray" }, SENT: { label: "Sent", tone: "blue" },
    ACKED: { label: "Acknowledged", tone: "teal" }, EXECUTING: { label: "Executing", tone: "blue" },
    COMPLETED: { label: "Completed", tone: "green" }, FAILED: { label: "Failed", tone: "red" },
    CANCELLED: { label: "Cancelled", tone: "gray" },
  },
  supply: {
    PENDING: { label: "Pending", tone: "gray" }, PAIRED: { label: "Paired", tone: "blue" },
    IN_PROGRESS: { label: "In progress", tone: "teal" }, COMPLETED: { label: "Completed", tone: "green" },
    FAILED: { label: "Failed", tone: "red" }, CANCELLED: { label: "Cancelled", tone: "gray" },
  },
  slot: {
    EMPTY: { label: "Empty", tone: "gray" }, RESERVED: { label: "Reserved", tone: "amber" },
    OCCUPIED: { label: "Occupied", tone: "green" },
  },
  loglevel: {
    DEBUG: { label: "DEBUG", tone: "gray" }, INFO: { label: "INFO", tone: "blue" },
    WARNING: { label: "WARN", tone: "amber" }, ERROR: { label: "ERROR", tone: "red" },
    CRITICAL: { label: "CRIT", tone: "red" },
  },
  mapstatus: {
    DRAFT: { label: "Draft", tone: "gray" }, ACTIVE: { label: "Active", tone: "green" },
    ARCHIVED: { label: "Archived", tone: "gray" },
  },
  dispatch: {
    PENDING: { label: "Pending", tone: "gray" }, SENT: { label: "Sent", tone: "blue" },
    ACKNOWLEDGED: { label: "Acknowledged", tone: "teal" }, EXECUTING: { label: "Executing", tone: "blue" },
    FINISHED: { label: "Finished", tone: "green" }, FAILED: { label: "Failed", tone: "red" },
  },
};

function statusBadge(map, key) {
  const m = map[key] || { label: key || "—", tone: "gray" };
  return `<span class="badge tone-${m.tone}">${esc(m.label)}</span>`;
}

/* ---------- Shared data table ---------- */
function renderTable(container, opts = {}) {
  const { columns = [], rows = [], actions = [], empty = "No data", onRow } = opts;
  container.innerHTML = "";
  if (!rows.length) { container.appendChild(el("div", { class: "empty" }, empty)); return; }
  const tbl = el("table", { class: "data" });
  tbl.appendChild(el("thead", {}, el("tr", {},
    columns.map((c) => el("th", { style: c.width ? `width:${c.width}` : "" }, c.label)))));
  const tbody = el("tbody");
  rows.forEach((row, i) => {
    const tr = el("tr");
    if (onRow) tr.addEventListener("click", () => onRow(row));
    for (const c of columns) {
      let val = c.badge ? statusBadge(c.map, c.get ? c.get(row) : row[c.key]) : (c.render ? c.render(row, i) : (c.get ? c.get(row) : row[c.key]));
      if (val == null || val === "") val = "—";
      const td = el("td", { class: [(c.num ? "num" : ""), (c.mono ? "mono" : "")].filter(Boolean).join(" ") });
      td.innerHTML = val;
      tr.appendChild(td);
    }
    if (actions.length) {
      const td = el("td", { class: "actions" });
      const box = el("div", { class: "row-actions" });
      actions.forEach((a) => {
        if (a.visible && !a.visible(row)) return;
        const b = el("button", { class: "btn btn-sm " + (a.cls || "btn-ghost"), text: a.label, title: a.title || a.label });
        b.addEventListener("click", (e) => { e.stopPropagation(); a.handler(row); });
        box.appendChild(b);
      });
      td.appendChild(box);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  });
  tbl.appendChild(tbody);
  container.appendChild(el("div", { class: "table-wrap" }, tbl));
}

function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ---------- CrudPage: generic CRUD scaffold ---------- */
class CrudPage {
  constructor(o) {
    this.endpoint = o.endpoint;
    this.pk = o.pk || "id";
    this.pkFn = o.pkFn || null;
    this.columns = o.columns || [];
    this.fields = o.fields || [];
    this.searchKeys = o.searchKeys || [];
    this.filters = o.filters || [];
    this.title = o.title || "Record";
    this.canCreate = o.canCreate !== false;
    this.canEdit = o.canEdit !== false;
    this.canDelete = o.canDelete !== false;
    this.customActions = o.customActions || [];
    this.toolbar = o.toolbar || null;
    this.onChanged = o.onChanged || null;
    this.beforeSubmit = o.beforeSubmit || null;
    this.listEl = typeof o.listEl === "string" ? $(o.listEl) : o.listEl;
    this.rows = [];
    this._search = "";
    this._filters = {};
    this._build();
  }

  _build() {
    this.listEl.innerHTML = "";
    const bar = el("div", { class: "panel-tools toolbar" });
    if (this.searchKeys.length) {
      const s = el("input", { type: "search", placeholder: "Search…", style: "min-width:170px" });
      s.addEventListener("input", () => { this._search = s.value.trim().toLowerCase(); this._render(); });
      bar.appendChild(s);
    }
    for (const f of this.filters) {
      const sel = el("select");
      sel.appendChild(el("option", { value: "" }, f.all || "All"));
      (f.options || []).forEach((o) => sel.appendChild(el("option", { value: o.v }, o.l)));
      sel.addEventListener("change", () => { this._filters[f.key] = sel.value; this._render(); });
      bar.appendChild(sel);
    }
    bar.appendChild(el("span", { class: "spacer" }));
    if (typeof this.toolbar === "function") this.toolbar(bar);
    if (this.canCreate) {
      const b = el("button", { class: "btn btn-primary btn-sm", html: `＋ ${this.title}` });
      b.addEventListener("click", () => this.openForm(null));
      bar.appendChild(b);
    }
    this.countEl = el("span", { class: "small muted" });
    bar.appendChild(this.countEl);
    this.listEl.appendChild(bar);
    this.bodyEl = el("div", { style: "padding:0" });
    this.listEl.appendChild(this.bodyEl);
  }

  async load() {
    try {
      let data = await api.get(this.endpoint);
      if (!Array.isArray(data)) data = data.results || data.rows || [];
      this.rows = data;
    } catch (e) { this.rows = []; toast(`Error loading ${this.title}: ` + e.message, "error"); }
    this._render();
  }

  _filtered() {
    return this.rows.filter((r) => {
      for (const k in this._filters) {
        const v = this._filters[k];
        if (v && String(r[k] ?? "") !== v) return false;
      }
      if (this._search) {
        const hay = this.searchKeys.map((k) => String(r[k] ?? "")).join(" ").toLowerCase();
        if (!hay.includes(this._search)) return false;
      }
      return true;
    });
  }

  _render() {
    const rows = this._filtered();
    this.countEl.textContent = `${rows.length}/${this.rows.length}`;
    const cols = this.columns.map((c) => {
      const col = { label: c.label, key: c.key, num: c.num, mono: c.mono, width: c.width, render: c.render, get: c.get };
      if (c.badge) col.render = (r) => statusBadge(c.map, c.get ? c.get(r) : r[c.key]);
      return col;
    });
    const actions = [];
    if (this.canEdit) actions.push({ label: "Edit", handler: (r) => this.openForm(r) });
    if (this.canDelete) actions.push({ label: "Delete", cls: "btn-danger", handler: (r) => this.remove(r) });
    for (const a of this.customActions) actions.push(a);
    renderTable(this.bodyEl, { columns: cols, rows, actions, empty: "No data" });
  }

  _pkVal(row) { return this.pkFn ? this.pkFn(row) : row[this.pk]; }

  async openForm(row) {
    const isNew = !row;
    const defs = [];
    for (const f of this.fields) {
      let opts = f.options;
      if (typeof opts === "function") { try { opts = await opts(); } catch (_) { opts = []; } }
      defs.push(Object.assign({}, f, { options: Array.isArray(opts) ? opts : (opts || []) }));
    }
    const grid = el("div", { class: "form-grid" });
    for (let i = 0; i < defs.length; i++) {
      const f = defs[i];
      const cur = row ? (row[f.key] ?? "") : (f.default ?? "");
      const wrap = el("div", { class: "field " + (f.full ? "full" : "") });
      wrap.appendChild(el("label", { html: f.label + (f.required ? ' <span class="req">*</span>' : "") }));
      const id = "fld_" + i;
      if (f.type === "select") {
        const sel = el("select", { id });
        sel.appendChild(el("option", { value: "" }, f.placeholder || "— select —"));
        for (const o of f.options) sel.appendChild(el("option", { value: o.v }, o.l));
        sel.value = String(cur ?? "");
        wrap.appendChild(sel);
      } else if (f.type === "checkbox") {
        const chk = el("input", { type: "checkbox", id });
        chk.checked = !!cur;
        wrap.appendChild(el("label", { class: "check", style: "text-transform:none" }, chk, f.placeholder || ""));
      } else if (f.type === "textarea" || f.type === "json") {
        const ta = el("textarea", { id, placeholder: f.placeholder || "" });
        ta.value = f.type === "json" && typeof cur === "object" ? JSON.stringify(cur, null, 2) : String(cur ?? "");
        wrap.appendChild(ta);
      } else if (f.type === "datetime") {
        wrap.appendChild(el("input", { type: "datetime-local", id, value: toLocalInput(cur) }));
      } else {
        const inp = el("input", { id, type: f.type === "number" ? "number" : "text", placeholder: f.placeholder || "", value: String(cur ?? "") });
        if (f.type === "number") inp.step = "any";
        if (f.readOnly) inp.disabled = true;
        wrap.appendChild(inp);
      }
      if (f.hint) wrap.appendChild(el("div", { class: "hint" }, f.hint));
      grid.appendChild(wrap);
    }
    openModal(`
      <h2>${isNew ? "New " : "Edit "} ${esc(this.title)}</h2>
      <div id="frm-grid"></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="frm-cancel">Cancel</button>
        <button class="btn btn-primary" id="frm-save">${isNew ? "Create" : "Save"}</button>
      </div>`);
    $("frm-grid").appendChild(grid);
    $("frm-cancel").onclick = closeModal;
    $("frm-save").onclick = () => this.submit(row, defs);
  }

  _collect(defs) {
    const data = {};
    defs.forEach((f, i) => {
      const node = $("fld_" + i);
      if (!node) return;
      let v;
      if (f.type === "checkbox") v = node.checked;
      else if (f.type === "number") v = node.value === "" ? null : Number(node.value);
      else if (f.type === "json") { try { v = node.value.trim() ? JSON.parse(node.value) : (f.default ?? {}); } catch (_) { v = node.value; } }
      else if (f.type === "datetime") v = node.value || null;
      else v = node.value;
      if ((v === "" || v == null) && f.required) throw new Error(`Missing field “${f.label}”`);
      if (v === "") v = null;
      data[f.key] = v;
    });
    return data;
  }

  async submit(row, defs) {
    let data;
    try { data = this._collect(defs); } catch (e) { toast(e.message, "error"); return; }
    if (this.beforeSubmit) { try { data = await this.beforeSubmit(data, row, !row); } catch (e) { toast(e.message, "error"); return; } }
    try {
      if (row) {
        await api.patch(`${this.endpoint}${encodeURIComponent(this._pkVal(row))}/`, data);
        toast("Updated", "ok");
      } else {
        await api.post(this.endpoint, data);
        toast("Created", "ok");
      }
      closeModal();
      await this.load();
      if (this.onChanged) this.onChanged();
    } catch (e) { toast("Error: " + e.message, "error"); }
  }

  async remove(row) {
    const ok = await confirmDialog("Delete " + this.title, `Delete <b>${esc(this._pkVal(row))}</b>? This action cannot be undone.`, "Delete");
    if (!ok) return;
    try {
      await api.del(`${this.endpoint}${encodeURIComponent(this._pkVal(row))}/`);
      toast("Deleted", "ok");
      await this.load();
      if (this.onChanged) this.onChanged();
    } catch (e) { toast("Error: " + e.message, "error"); }
  }
}

/* ---------- Shell: theme, clock, health ---------- */
function setChip(el, ok, label) {
  if (!el) return;
  el.className = "chip " + (ok ? "ok" : "err");
  const b = el.querySelector("b");
  if (b) b.textContent = label;
}

async function loadHealth() {
  try {
    const r = await api.get("/api/health/ready/");
    const inst = $("instance-name");
    if (inst) inst.textContent = r.instance || "ACS";
    setChip($("db-chip"), !!(r.database && r.database.ok), "DB");
    const workers = r.workers || {}; const stale = r.stale_workers || [];
    setChip($("worker-chip"), !stale.length && Object.keys(workers).length > 0, stale.length ? `Worker:${stale.join(",")}` : "Worker");
    const outbox = r.outbox_pending ?? 0;
    const oc = $("outbox-count"); if (oc) oc.textContent = outbox;
    setChip($("outbox-chip"), outbox < 5000, "Outbox");
    const side = $("side-api"); if (side) side.textContent = r.status || "ok";
    const sideDot = document.querySelector("#sidebar .side-stat .dot");
    if (sideDot) sideDot.className = "dot " + (r.database && r.database.ok ? "ok" : "err");
    return r;
  } catch (_) { setChip($("db-chip"), false, "DB"); return null; }
}

function initShell() {
  const theme = localStorage.getItem("acs-theme") || "dark";
  document.documentElement.setAttribute("data-theme", theme);
  const btnTheme = $("btn-theme");
  if (btnTheme) {
    btnTheme.textContent = theme === "dark" ? "☾" : "☀";
    btnTheme.onclick = () => {
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      localStorage.setItem("acs-theme", cur);
      btnTheme.textContent = cur === "dark" ? "☾" : "☀";
    };
  }
  const fs = $("btn-fullscreen");
  if (fs) fs.onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
  const clock = $("clock");
  if (clock) {
    const tick = () => { clock.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false }); };
    tick(); setInterval(tick, 1000);
  }
  loadHealth().catch(() => {});
  setInterval(() => loadHealth().catch(() => {}), 5000);
}

initShell();




