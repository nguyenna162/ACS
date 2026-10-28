/* ACS · AMR — fleet management: RobotProfile CRUD + telemetry + topics + commands */
"use strict";

const ROBOT_STATES = ["INIT", "IDLE", "RUNNING", "WARNING", "BLOCKED", "ERROR", "OFFLINE"].map((v) => ({ v, l: v }));
const OP_MODES = ["AUTOMATIC", "SEMIAUTOMATIC", "INTERVENED", "MANUAL", "SERVICE"].map((v) => ({ v, l: v }));

async function nodeOptions() { const ns = await api.get("/api/nodes/"); return ns.map((n) => ({ v: n.node_id, l: n.node_id })); }
async function instantAction(serial, actionType) {
  try { await api.post("/api/instant-actions/", { serial_number: serial, action_type: actionType }); toast(`Sent ${actionType} → ${serial}`, "ok"); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

async function loadKPIs() {
  try {
    const d = await api.get("/api/robots/fleet_summary/");
    const by = d.by_state || {};
    const cards = [
      ["Total AMRs", d.total ?? 0, `online ${d.online ?? 0}`, ""],
      ["Idle", d.idle ?? 0, "ready", "ok"],
      ["Running", by.RUNNING || 0, "executing", "info"],
      ["Low battery", (d.low_battery || []).length, "< 25%", "warn"],
      ["Errors", by.ERROR || 0, "attention needed", "danger"],
    ];
    $("amr-kpis").innerHTML = cards.map(([l, v, s, c]) => `<div class="kpi ${c}"><div class="kpi-label">${l}</div><div class="kpi-value">${v}</div><div class="kpi-sub">${s}</div></div>`).join("");
  } catch (_) {}
}

/* ---------- CRUD: RobotProfile ---------- */
const robotsPage = new CrudPage({
  endpoint: "/api/robots/", pk: "serial_number", title: "AMR", listEl: "crud-robots",
  searchKeys: ["serial_number", "manufacturer", "amr_model", "order_id"],
  filters: [{ key: "operational_state", label: "State", options: ROBOT_STATES }],
  columns: [
    { label: "Serial", key: "serial_number", mono: true },
    { label: "Model", key: "amr_model" },
    { label: "State", key: "operational_state", badge: true, map: STATUS.robot },
    { label: "Battery", key: "battery_level", num: true, render: (r) => `${Math.round(r.battery_level || 0)}%` },
    { label: "Node", key: "last_node_id", mono: true },
    { label: "Order", key: "order_id", mono: true },
    { label: "Online", key: "is_online", render: (r) => r.is_online ? '<span class="badge tone-green">On</span>' : '<span class="badge tone-gray">Off</span>' },
  ],
  fields: [
    { key: "serial_number", label: "Serial *", type: "text", required: true, placeholder: "AMR_01" },
    { key: "manufacturer", label: "Manufacturer", type: "text", default: "SEVT_Robotics" },
    { key: "amr_model", label: "Model", type: "text" },
    { key: "interface", label: "Interface", type: "text", default: "VDA5050" },
    { key: "version", label: "Version", type: "text", default: "2.0.0" },
    { key: "ip_address", label: "IP", type: "text", placeholder: "192.168.1.10" },
    { key: "port", label: "Port", type: "number", default: 1883 },
    { key: "operational_state", label: "State", type: "select", options: ROBOT_STATES, default: "INIT" },
    { key: "operating_mode", label: "Mode", type: "select", options: OP_MODES, default: "AUTOMATIC" },
    { key: "battery_level", label: "Battery (%)", type: "number", default: 100 },
    { key: "current_node", label: "Current node", type: "select", options: nodeOptions },
    { key: "pos_x", label: "X", type: "number", default: 0 },
    { key: "pos_y", label: "Y", type: "number", default: 0 },
    { key: "theta", label: "Angle", type: "number", default: 0 },
    { key: "zone_set_id", label: "Zone Set", type: "text" },
  ],
  customActions: [
    { label: "⏸", title: "Pause", handler: (r) => instantAction(r.serial_number, "startPause") },
    { label: "▶", title: "Resume", handler: (r) => instantAction(r.serial_number, "stopPause") },
    { label: "Telemetry", handler: (r) => showTelemetry(r.serial_number) },
    { label: "Topics", handler: (r) => showTopics(r.serial_number) },
  ],
  onChanged: loadKPIs,
});

/* ---------- Telemetry & topic ---------- */
function kv(pairs) {
  return `<table class="data" style="font-size:12px"><tbody>` +
    pairs.map(([k, v]) => `<tr><td style="color:var(--muted);width:130px">${esc(k)}</td><td class="mono">${esc(v ?? "—")}</td></tr>`).join("") + `</tbody></table>`;
}
async function showTelemetry(serial) {
  $("telemetry-title").textContent = `Telemetry — ${serial}`;
  try {
    const rows = await api.get(`/api/robots/${encodeURIComponent(serial)}/telemetry/?limit=20`);
    if (!rows.length) { $("telemetry-panel").innerHTML = `<div class="empty">No telemetry</div>`; return; }
    const latest = rows[0];
    const pos = latest.amr_position || {};
    const batt = latest.battery_state || {};
    const vel = latest.velocity || {};
    $("telemetry-panel").innerHTML = kv([
      ["Order", latest.order_id], ["Mode", latest.operating_mode], ["Velocity vx", fmtNum(latest.velocity_vx)],
      ["Position", pos.x != null ? `${fmtNum(pos.x)} , ${fmtNum(pos.y)}` : "—"],
      ["Battery", batt.battery_percent != null ? `${Math.round(batt.battery_percent)}%` : "—"],
      ["Driving", latest.driving ? "Yes" : "No"], ["Paused", latest.paused ? "Yes" : "No"],
      ["Errors", (latest.error_codes || []).join(", ") || "—"], ["Recorded", fmtTime(latest.recorded_at)],
    ]) + `<div class="section-title mt-12">Recent history</div>` +
      `<table class="data"><thead><tr><th>Time</th><th>Order</th><th>Mode</th><th>Battery</th></tr></thead><tbody>` +
      rows.slice(0, 10).map((t) => `<tr><td class="mono">${fmtTime(t.recorded_at)}</td><td class="mono">${esc(t.order_id || "—")}</td><td>${esc(t.operating_mode || "—")}</td><td class="num">${(t.battery_state || {}).battery_percent != null ? Math.round(t.battery_state.battery_percent) + "%" : "—"}</td></tr>`).join("") +
      `</tbody></table>`;
  } catch (e) { $("telemetry-panel").innerHTML = `<div class="empty">Error: ${esc(e.message)}</div>`; }
}
async function showTopics(serial) {
  try {
    const rows = await api.get(`/api/robots/${encodeURIComponent(serial)}/topics/`);
    $("topics-panel").innerHTML = `<table class="data"><thead><tr><th>Type</th><th>Topic</th><th>Header ID</th></tr></thead><tbody>` +
      rows.map((t) => `<tr><td>${statusBadge({ STATE: { label: "STATE", tone: "blue" }, ORDER: { label: "ORDER", tone: "teal" }, INSTANT_ACTIONS: { label: "INSTANT", tone: "amber" }, CONNECTION: { label: "CONN", tone: "gray" }, FACTSHEET: { label: "FACT", tone: "gray" } }[t.topic_type] || { label: t.topic_type, tone: "gray" })}</td><td class="mono" style="word-break:break-all">${esc(t.topic)}</td><td class="num">${t.header_id}</td></tr>`).join("") +
      `</tbody></table>`;
  } catch (e) { $("topics-panel").innerHTML = `<div class="empty">Error: ${esc(e.message)}</div>`; }
}

async function init() {
  $("btn-refresh-robots").onclick = () => robotsPage.load();
  await Promise.allSettled([loadKPIs(), robotsPage.load()]);
  setInterval(loadKPIs, 6000);
}
init();

