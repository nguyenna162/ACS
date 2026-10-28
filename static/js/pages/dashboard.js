/* ACS · Dashboard — map + fleet + orders + alerts monitoring */
"use strict";

const els = {
  kpis: $("kpis"), fleetList: $("fleet-list"), fleetSearch: $("fleet-search"), fleetFilter: $("fleet-filter"),
  ordersList: $("orders-list"), alertsList: $("alerts-list"), detail: $("detail-panel"),
  mapScale: $("map-scale"), mapCoord: $("map-coord"), legend: $("map-legend"),
  statusLeft: $("status-left"), statusRight: $("status-right"),
};

const state = { orders: [], alerts: [], selected: null, robots: new Map() };

const engine = new MapEngine($("map-canvas"), {
  onCursor: (w) => {
    els.mapCoord.textContent = `x: ${w.x.toFixed(1)} · y: ${w.y.toFixed(1)}`;
    const step = engine._niceStep(80 / engine.scale);
    els.mapScale.textContent = `${step} m`;
  },
  onSelect: (hit) => { state.selected = hit; renderDetail(); renderFleet(); },
});

function setStatus(l, r) { els.statusLeft.textContent = l; if (r != null) els.statusRight.textContent = r; }

async function loadMap() {
  const [full, zones, buffers] = await Promise.all([
    api.get("/api/topology/full_map/"), api.get("/api/zones/"), api.get("/api/buffer-slots/"),
  ]);
  engine.setMap({ nodes: full.nodes || [], edges: full.edges || [], zones, bufferSlots: buffers });
  engine.fit();
  els.legend.innerHTML = engine.legendHtml();
  setStatus("Ready", `${(full.nodes || []).length} nodes · ${(full.edges || []).length} edges · ${buffers.length} buffers`);
}

async function loadFleet() {
  try {
    const d = await api.get("/api/robots/fleet_summary/");
    state.robots = new Map((d.robots || []).map((r) => [r.serial_number, r]));
    engine.setRobots(d.robots || []);
    renderKPIs(d);
    renderFleet();
  } catch (_) {}
}

async function loadOrders() { try { state.orders = await api.get("/api/orders/"); renderOrders(); } catch (_) {} }

/* ---------- KPI ---------- */
function renderKPIs(d) {
  const by = d.by_state || {};
  const cards = [
    ["AMR", d.total ?? 0, `online ${d.online ?? 0}`, ""],
    ["Idle", d.idle ?? 0, "ready", "ok"],
    ["Running", by.RUNNING || 0, "executing", "info"],
    ["Low battery", (d.low_battery || []).length, "< 25%", "warn"],
  ];
  els.kpis.innerHTML = cards.map(([l, v, s, cls]) =>
    `<div class="kpi ${cls}"><div class="kpi-label">${l}</div><div class="kpi-value">${v}</div><div class="kpi-sub">${s}</div></div>`).join("");
}

/* ---------- Fleet ---------- */
function filteredFleet() {
  const q = els.fleetSearch.value.trim().toLowerCase();
  const f = els.fleetFilter.value;
  return [...state.robots.values()].filter((r) => {
    if (f && r.operational_state !== f) return false;
    if (!q) return true;
    return `${r.serial_number} ${r.order_id || ""}`.toLowerCase().includes(q);
  }).sort((a, b) => a.serial_number.localeCompare(b.serial_number));
}

function renderFleet() {
  const rows = filteredFleet().map((r) => {
    const batt = Math.round(r.battery_level || 0);
    const bc = batt > 50 ? "var(--ok)" : batt > 25 ? "var(--warn)" : "var(--danger)";
    const active = state.selected && state.selected.kind === "robot" && state.selected.id === r.serial_number;
    return `<li class="${active ? "active" : ""}" data-kind="robot" data-id="${esc(r.serial_number)}">
      <div class="avatar" style="min-width:26px;text-align:center">◈</div>
      <div class="info">
        <div class="top"><span class="serial">${esc(r.serial_number)}</span>${statusBadge(STATUS.robot, r.operational_state)}</div>
        <div class="meta">${esc(r.current_node_id || r.last_node_id || "—")}${r.order_id ? " · " + esc(r.order_id) : ""}</div>
        <div style="height:4px;background:var(--border);border-radius:2px;margin-top:4px"><span style="display:block;width:${batt}%;height:100%;background:${bc}"></span></div>
      </div>
      <span class="mono small">${batt}%</span>
    </li>`;
  }).join("");
  els.fleetList.innerHTML = rows || `<li class="empty">No AMRs</li>`;
}

/* ---------- Orders ---------- */
function renderOrders() {
  const active = state.orders.filter((o) => ["CREATED", "ASSIGNED", "EXECUTING"].includes(o.order_status));
  const done = state.orders.filter((o) => !["CREATED", "ASSIGNED", "EXECUTING"].includes(o.order_status));
  const rows = [...active, ...done].slice(0, 60).map((o) => `
    <li data-kind="order" data-id="${esc(o.order_id)}">
      <div class="info">
        <div class="top"><span class="serial">${esc(o.order_id)}</span>${statusBadge(STATUS.order, o.order_status)}</div>
        <div class="meta">${esc(o.src_slot)} → ${esc(o.dst_slot)}${o.robot_serial ? " · " + esc(o.robot_serial) : ""}</div>
      </div>
    </li>`).join("");
  els.ordersList.innerHTML = rows || `<li class="empty">No orders</li>`;
}

/* ---------- Alerts ---------- */
function pushAlert(text, kind = "info") {
  state.alerts.unshift({ text, kind, t: Date.now() });
  if (state.alerts.length > 100) state.alerts.length = 100;
  renderAlerts();
}
function renderAlerts() {
  els.alertsList.innerHTML = state.alerts.length
    ? state.alerts.map((a) => `<li><div class="info"><div class="top"><span class="serial" style="color:${a.kind === "error" ? "var(--danger)" : "var(--warn)"}">${esc(a.text)}</span></div><div class="meta">${fmtTime(new Date(a.t).toISOString())}</div></div></li>`).join("")
    : `<li class="empty">No alerts</li>`;
}

/* ---------- Detail ---------- */
function kv(pairs) {
  return `<table class="data" style="font-size:12px"><tbody>` +
    pairs.map(([k, v]) => `<tr><td style="color:var(--muted);width:120px">${esc(k)}</td><td class="mono">${esc(v ?? "—")}</td></tr>`).join("") + `</tbody></table>`;
}
function renderDetail() {
  const s = state.selected;
  if (!s) { els.detail.innerHTML = ""; return; }
  if (s.kind === "robot") {
    const r = state.robots.get(s.id);
    if (!r) return;
    els.detail.innerHTML = `
      <div class="flex-between" style="padding:10px 12px;border-bottom:1px solid var(--border)">
        <b>${esc(r.serial_number)}</b>${statusBadge(STATUS.robot, r.operational_state)}
      </div>
      <div style="padding:10px 12px">${kv([
        ["Node", r.current_node_id || r.last_node_id], ["Order", r.order_id], ["Battery", `${Math.round(r.battery_level || 0)}%`],
        ["Mode", r.operating_mode], ["Position", `${fmtNum(r.pos_x)} , ${fmtNum(r.pos_y)}`], ["Online", r.is_online ? "Yes" : "No"],
      ])}
      <div class="flex" style="padding:0 12px 12px">
        <button class="btn btn-sm btn-danger" onclick="sendInstantAction('${esc(r.serial_number)}','startPause')">⏸ Pause</button>
        <button class="btn btn-sm" onclick="sendInstantAction('${esc(r.serial_number)}','stopPause')">▶ Resume</button>
        <button class="btn btn-sm" onclick="sendInstantAction('${esc(r.serial_number)}','cancelOrder')">✕ Cancel order</button>
      </div></div>`;
  } else if (s.kind === "node") {
    const n = engine.nodesById.get(s.id);
    if (!n) return;
    const slot = (engine.bufferSlots || []).find((b) => b.node_id === n.node_id);
    els.detail.innerHTML = `<div class="flex-between" style="padding:10px 12px;border-bottom:1px solid var(--border)"><b>${esc(n.node_id)}</b><span class="muted">${esc(n.node_type)}</span></div>
      <div style="padding:10px 12px">${kv([
        ["Name", n.name], ["Position", `${fmtNum(n.pos_x)} , ${fmtNum(n.pos_y)}`], ["Angle", fmtNum(n.theta)],
        ["Zone", n.zone || n.traffic_zone], ["Held by", n.locked_by],
        ...(slot ? [["Buffer", `${slot.slot_id} [${slot.slot_status}]`]] : []),
      ])}</div>`;
  }
}

async function sendInstantAction(serial, actionType) {
  try { await api.post("/api/instant-actions/", { serial_number: serial, action_type: actionType }); toast(`Sent ${actionType} → ${serial}`, "ok"); }
  catch (e) { toast("Error: " + e.message, "error"); }
}
async function cancelOrder(orderId) {
  try { await api.post(`/api/orders/${orderId}/cancel/`, {}); toast(`Cancelled ${orderId}`, "ok"); loadOrders(); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

/* ---------- New order modal ---------- */
async function openNewOrder() {
  let slots; try { slots = await api.get("/api/buffer-slots/"); } catch (e) { toast("Error loading buffers: " + e.message, "error"); return; }
  const opts = (st) => slots.filter((s) => s.slot_status === st).map((s) => `<option value="${esc(s.slot_id)}">${esc(s.slot_id)} — ${esc(s.name || s.buffer_type || "")}</option>`).join("");
  openModal(`<h2>New transport order</h2>
    <div class="form-grid">
      <div class="field full"><label>Source slot (occupied)</label><select id="f-src">${opts("OCCUPIED")}</select></div>
      <div class="field full"><label>Destination slot (empty)</label><select id="f-dst">${opts("EMPTY")}</select></div>
      <div class="field"><label>Priority</label><input id="f-prio" type="number" min="1" max="100" value="1"></div>
      <div class="field"><label>Order type</label><select id="f-type"><option>TRANSPORT</option><option>TRANSFER</option></select></div>
    </div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="submitNewOrder()">Create order</button></div>`);
}
async function submitNewOrder() {
  const body = { src_slot: $("f-src").value, dst_slot: $("f-dst").value, priority: parseInt($("f-prio").value || "1", 10), order_type: $("f-type").value };
  try { const o = await api.post("/api/orders/", body); closeModal(); toast(`Created ${o.order_id}`, "ok"); loadOrders(); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

/* ---------- WebSocket ---------- */
let reconnectTimer = null;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws;
  try { ws = new WebSocket(`${proto}://${location.host}/ws/robot/telemetry/`); } catch (_) { scheduleReconnect(); return; }
  ws.onopen = () => setConn(true, "Live");
  ws.onclose = () => { setConn(false, "Disconnected"); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
  ws.onmessage = (ev) => { try { handleWS(JSON.parse(ev.data)); } catch (_) {} };
}
function scheduleReconnect() { clearTimeout(reconnectTimer); reconnectTimer = setTimeout(connectWS, 3000); }
function setConn(ok, label) {
  const c = $("conn-chip"); if (!c) return;
  c.className = "chip " + (ok ? "ok" : "err");
  c.querySelector("b").textContent = label;
}
function handleWS(msg) {
  if (msg.type === "initial_snapshot" || msg.type === "fleet_snapshot") {
    state.robots = new Map((msg.robots || []).map((r) => [r.serial_number, r]));
    engine.setRobots(msg.robots || []);
    renderFleet(); renderKPIs(summaryFromRobots());
  } else if (msg.type === "robot_update") {
    const r = msg.data; state.robots.set(r.serial_number, r); engine.setRobots([r]); renderFleet();
    if (state.selected && state.selected.kind === "robot" && state.selected.id === r.serial_number) renderDetail();
  } else if (msg.type === "realtime_event") {
    handleEvent(msg.topic, msg.data);
  }
}
function summaryFromRobots() {
  const rs = [...state.robots.values()], by = {};
  for (const r of rs) by[r.operational_state] = (by[r.operational_state] || 0) + 1;
  return { total: rs.length, online: rs.filter((r) => r.is_online).length, idle: by.IDLE || 0, by_state: by, low_battery: rs.filter((r) => (r.battery_level || 0) < 25).map((r) => r.serial_number) };
}
function handleEvent(topic, data) {
  if (topic === "order") { loadOrders(); if (data && data.event) pushAlert(`Order: ${data.event} ${data.order_id || ""}`); }
  else if (topic === "traffic") pushAlert(`Traffic: ${data.event || ""}`, "error");
  else if (topic === "alert") pushAlert(data.message || JSON.stringify(data));
  else if (topic === "robot_error") pushAlert(`AMR ${data.serial_number || ""}: error`, "error");
}

/* ---------- Tabs & buttons ---------- */
function wire() {
  document.querySelectorAll("#inspector-tabs .tab-btn").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#inspector-tabs .tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll("#inspector .tab-pane").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); $("tab-" + b.dataset.tab).classList.add("active");
    };
  });
  els.fleetSearch.oninput = renderFleet;
  els.fleetFilter.onchange = renderFleet;
  els.fleetList.onclick = (e) => { const li = e.target.closest("li[data-kind]"); if (!li) return; if (li.dataset.kind === "robot") { state.selected = { kind: "robot", id: li.dataset.id }; renderFleet(); renderDetail(); } };
  els.ordersList.onclick = (e) => { const li = e.target.closest("li[data-kind='order']"); if (li) { state.selected = null; renderFleet(); showOrderDetail(li.dataset.id); } };
  $("btn-zoom-in").onclick = () => engine.zoomBy(1.25);
  $("btn-zoom-out").onclick = () => engine.zoomBy(1 / 1.25);
  $("btn-zoom-fit").onclick = () => engine.fit();
  $("btn-grid").onclick = () => { engine.showGrid = !engine.showGrid; };
  $("btn-new-order").onclick = openNewOrder;
  $("btn-refresh-orders").onclick = loadOrders;
  $("btn-clear-alerts").onclick = () => { state.alerts = []; renderAlerts(); };
  window.addEventListener("resize", () => engine.resize());
}

function showOrderDetail(orderId) {
  const o = state.orders.find((x) => x.order_id === orderId); if (!o) return;
  els.detail.innerHTML = `<div class="flex-between" style="padding:10px 12px;border-bottom:1px solid var(--border)"><b>${esc(orderId)}</b>${statusBadge(STATUS.order, o.order_status)}</div>
    <div style="padding:10px 12px">${kv([["Robot", o.robot_serial], ["Source", o.src_slot], ["Destination", o.dst_slot], ["Priority", o.priority], ["Created", fmtTime(o.created_at)]])}
    ${["CREATED", "ASSIGNED", "EXECUTING"].includes(o.order_status) ? `<div class="mt-8"><button class="btn btn-sm btn-danger" onclick="cancelOrder('${esc(orderId)}')">Cancel order</button></div>` : ""}</div>`;
}

async function init() {
  wire();
  setStatus("Loading data…");
  await Promise.allSettled([loadMap(), loadFleet(), loadOrders()]);
  connectWS();
  setInterval(() => { loadFleet(); }, 4000);
  setInterval(() => { loadOrders(); }, 5000);
}
init();


