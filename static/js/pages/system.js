/* ACS · System — system admin: readiness, metrics, workers, logs */
"use strict";

function kv(pairs) {
  return `<table class="data" style="font-size:12px"><tbody>` +
    pairs.map(([k, v]) => `<tr><td style="color:var(--muted);width:170px">${esc(k)}</td><td class="mono">${v ?? "—"}</td></tr>`).join("") + `</tbody></table>`;
}

let lastReadiness = null;

async function loadSystem() {
  try {
    const [ready, met] = await Promise.all([api.get("/api/health/ready/"), api.get("/api/health/metrics/")]);
    lastReadiness = ready;
    renderKPIs(ready, met);
    renderReadiness(ready);
    renderWorkers(ready.workers || {});
  } catch (e) { $("readiness-panel").innerHTML = `<div class="empty">Error: ${esc(e.message)}</div>`; }
}

function renderKPIs(r, m) {
  const stale = (r.stale_workers || []).length;
  const cards = [
    ["Instance", m.instance, `env: ${r.env}`, ""],
    ["AMR", (m.robots || {}).total ?? 0, "total", "info"],
    ["Orders running", (m.orders || {}).in_flight ?? 0, `queue ${(m.orders || {}).queued ?? 0}`, "accent"],
    ["Outbox pending", (m.mqtt || {}).outbox_pending ?? 0, `failed ${(m.mqtt || {}).outbox_failed ?? 0}`, (m.mqtt || {}).outbox_pending > 100 ? "warn" : "ok"],
    ["Stale workers", stale, "needs check", stale ? "danger" : "ok"],
  ];
  $("sys-kpis").innerHTML = cards.map(([l, v, s, c]) => `<div class="kpi ${c}"><div class="kpi-label">${l}</div><div class="kpi-value">${v}</div><div class="kpi-sub">${s}</div></div>`).join("");
}

function renderReadiness(r) {
  const db = r.database || {};
  const statusTone = r.status === "ok" ? "green" : r.status === "degraded" ? "amber" : "red";
  $("readiness-panel").innerHTML = kv([
    ["Status", statusBadge({ ok: { label: "OK", tone: "green" }, degraded: { label: "DEGRADED", tone: "amber" }, unavailable: { label: "UNAVAILABLE", tone: "red" } }, r.status)],
    ["Database", db.ok ? `${db.vendor} — OK` : `${db.vendor} — ERROR`],
    ["DB detail", db.detail],
    ["Outbox pending", r.outbox_pending],
    ["Stale workers", (r.stale_workers || []).join(", ") || "—"],
    ["Checked at", fmtDateTime(r.time)],
  ]);
}

function renderWorkers(workers) {
  const entries = Object.entries(workers);
  if (!entries.length) { $("workers-panel").innerHTML = `<div class="empty">No worker heartbeat yet</div>`; return; }
  $("workers-panel").innerHTML = `<table class="data"><thead><tr><th>Component</th><th>Instance</th><th>Last seen</th><th>Age</th><th>State</th></tr></thead><tbody>` +
    entries.map(([name, w]) => `<tr>
      <td class="mono">${esc(name)}</td>
      <td class="mono">${esc(w.instance_id || "—")}</td>
      <td class="mono">${fmtTime(w.last_seen)}</td>
      <td class="num">${w.age_seconds}s</td>
      <td>${w.stale ? '<span class="badge tone-red">Stale</span>' : '<span class="badge tone-green">OK</span>'}</td>
    </tr>`).join("") + `</tbody></table>`;
}

/* ---------- System logs (read-only) ---------- */
const logsPage = new CrudPage({
  endpoint: "/api/system-logs/", pk: "id", title: "", listEl: "crud-logs",
  canCreate: false, canEdit: false, canDelete: false,
  searchKeys: ["message", "actor", "robot", "order"],
  filters: [{ key: "level", label: "Level", options: Object.entries(STATUS.loglevel).map(([v, m]) => ({ v, l: m.label })) }],
  columns: [
    { label: "Time", key: "created_at", render: (r) => fmtTime(r.created_at), mono: true },
    { label: "Level", key: "level", badge: true, map: STATUS.loglevel },
    { label: "Category", key: "category", mono: true },
    { label: "Message", key: "message" },
    { label: "Actor", key: "actor", mono: true },
    { label: "AMR", key: "robot", mono: true },
  ],
});

async function init() {
  $("btn-refresh-system").onclick = () => { loadSystem(); logsPage.load(); };
  await Promise.allSettled([loadSystem(), logsPage.load()]);
  setInterval(loadSystem, 8000);
}
init();
