/* ACS · Map Editor — visual CRUD for topology/node/edge/zone/buffer/action */
"use strict";

const els = {
  mapScale: $("map-scale"), mapCoord: $("map-coord"), legend: $("map-legend"), hint: $("map-hint"),
};

const NODE_TYPES = [
  { v: "WAYPOINT", l: "Waypoint" }, { v: "INTERSECTION", l: "Intersection" },
  { v: "BUFFER", l: "Buffer Point" }, { v: "CHARGER", l: "Charging Point" },
  { v: "PARKING", l: "Parking" }, { v: "LINE", l: "Production Line" },
];
const DIRECTIONS = [{ v: "UNI", l: "One-way" }, { v: "BI", l: "Two-way" }];
const BLOCKING = [{ v: "NONE", l: "None" }, { v: "SOFT", l: "Soft" }, { v: "HARD", l: "Hard" }];
const BUFFER_TYPES = [{ v: "STORAGE", l: "Storage" }, { v: "LINE", l: "Line" }, { v: "PICK", l: "Pick" }, { v: "DROP", l: "Drop" }, { v: "CHARGING", l: "Charging" }];
const TRAFFIC_ZONE_TYPES = ["LINE", "CROSSING", "BUFFER", "CHARGING", "PARKING", "BLOCKED", "SPEED_LIMIT"].map((v) => ({ v, l: v }));
const MAP_STATUS = [{ v: "DRAFT", l: "Draft" }, { v: "ACTIVE", l: "Active" }, { v: "ARCHIVED", l: "Archived" }];

const engine = new MapEngine($("map-canvas"), {
  editable: true,
  onCursor: (w) => {
    els.mapCoord.textContent = `x: ${w.x.toFixed(1)} · y: ${w.y.toFixed(1)}`;
    els.mapScale.textContent = `${engine._niceStep(80 / engine.scale)} m`;
  },
  onAddNode: (x, y) => addNodeAt(x, y),
  onConnect: (a, b) => addEdgeBetween(a, b),
  onMoveNode: (id, x, y) => { api.patch(`/api/nodes/${encodeURIComponent(id)}/`, { pos_x: x, pos_y: y }).catch(() => {}); },
});

async function nodeOptions() { const ns = await api.get("/api/nodes/"); return ns.map((n) => ({ v: n.node_id, l: n.node_id })); }
async function topologyOptions() { const ts = await api.get("/api/topology/"); return ts.map((t) => ({ v: t.map_id, l: `${t.map_name} (${t.map_id})` })); }
async function bufferZoneOptions() { const zs = await api.get("/api/buffer-zones/"); return zs.map((z) => ({ v: z.zone_id, l: z.zone_name })); }
async function mapBaseOptions() { const ms = await api.get("/api/maps/"); return ms.map((m) => ({ v: m.id, l: m.name })); }
async function zoneSetOptions() { const zs = await api.get("/api/zone-sets/"); return zs.map((z) => ({ v: z.id, l: z.name })); }

async function reloadMap() {
  try {
    const [full, zones, buffers] = await Promise.all([api.get("/api/topology/full_map/"), api.get("/api/zones/"), api.get("/api/buffer-slots/")]);
    engine.setMap({ nodes: full.nodes || [], edges: full.edges || [], zones, bufferSlots: buffers });
    els.legend.innerHTML = engine.legendHtml();
    $("status-right").textContent = `${(full.nodes || []).length} nodes · ${(full.edges || []).length} edges`;
  } catch (_) {}
}

/* ---------- CRUD: Node ---------- */
const nodesPage = new CrudPage({
  endpoint: "/api/nodes/", pk: "node_id", title: "Node", listEl: "crud-nodes",
  searchKeys: ["node_id", "name"],
  columns: [
    { label: "Node", key: "node_id", mono: true },
    { label: "Type", key: "node_type", render: (r) => `<span class="badge tone-teal">${esc(r.node_type)}</span>` },
    { label: "Name", key: "name" },
    { label: "x", key: "pos_x", num: true, render: (r) => fmtNum(r.pos_x) },
    { label: "y", key: "pos_y", num: true, render: (r) => fmtNum(r.pos_y) },
    { label: "Status", key: "is_occupied", render: (r) => r.is_occupied ? statusBadge(STATUS.slot, "OCCUPIED") : "—" },
  ],
  filters: [{ key: "node_type", label: "Type", options: NODE_TYPES }],
  fields: [
    { key: "node_id", label: "Node ID", type: "text", required: true, placeholder: "N_01" },
    { key: "map_topology", label: "Topology", type: "select", required: true, options: topologyOptions },
    { key: "node_type", label: "Node type", type: "select", options: NODE_TYPES, default: "WAYPOINT" },
    { key: "name", label: "Name", type: "text" },
    { key: "pos_x", label: "Position X", type: "number", required: true, default: 0 },
    { key: "pos_y", label: "Position Y", type: "number", required: true, default: 0 },
    { key: "theta", label: "Angle (rad)", type: "number", default: 0 },
    { key: "use_node", label: "In use", type: "checkbox", default: true },
    { key: "is_init", label: "Start point", type: "checkbox", default: false },
    { key: "description", label: "Description", type: "text", full: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Edge ---------- */
const edgesPage = new CrudPage({
  endpoint: "/api/edges/", pk: "edge_id", title: "Edge", listEl: "crud-edges",
  searchKeys: ["edge_id", "start_node", "end_node"],
  columns: [
    { label: "Edge", key: "edge_id", mono: true },
    { label: "From", key: "start_node", mono: true },
    { label: "To", key: "end_node", mono: true },
    { label: "Distance", key: "distance", num: true, render: (r) => fmtNum(r.distance, 1) },
    { label: "Dir", key: "direction", render: (r) => r.direction === "BI" ? "⇄" : "→" },
    { label: "Locked", key: "is_locked", render: (r) => r.is_locked ? "🔒" : "—" },
  ],
  fields: [
    { key: "edge_id", label: "Edge ID", type: "text", required: true, placeholder: "E_01" },
    { key: "start_node", label: "Start node", type: "select", required: true, options: nodeOptions },
    { key: "end_node", label: "End node", type: "select", required: true, options: nodeOptions },
    { key: "distance", label: "Distance (m)", type: "number", required: true },
    { key: "direction", label: "Direction", type: "select", options: DIRECTIONS, default: "UNI" },
    { key: "max_speed", label: "Max speed", type: "number", default: 1.2 },
    { key: "name", label: "Name", type: "text" },
    { key: "description", label: "Description", type: "text", full: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Zone (rectangle) ---------- */
const zonesPage = new CrudPage({
  endpoint: "/api/zones/", pk: "id", title: "Zone", listEl: "crud-zones",
  searchKeys: ["zone_name", "zone_id"],
  columns: [
    { label: "Name", key: "zone_name" },
    { label: "ID", key: "zone_id", mono: true },
    { label: "x", key: "pos_x", num: true, render: (r) => fmtNum(r.pos_x) },
    { label: "y", key: "pos_y", num: true, render: (r) => fmtNum(r.pos_y) },
    { label: "Width", key: "size_x", num: true, render: (r) => fmtNum(r.size_x) },
    { label: "Length", key: "size_y", num: true, render: (r) => fmtNum(r.size_y) },
    { label: "Status", key: "is_active", render: (r) => r.is_active ? '<span class="badge tone-green">On</span>' : '<span class="badge tone-gray">Off</span>' },
  ],
  fields: [
    { key: "zone_name", label: "Zone name", type: "text", required: true },
    { key: "zone_id", label: "Zone ID", type: "text" },
    { key: "pos_x", label: "Center X", type: "number", default: 0 },
    { key: "pos_y", label: "Center Y", type: "number", default: 0 },
    { key: "size_x", label: "Width X", type: "number", default: 0 },
    { key: "size_y", label: "Length Y", type: "number", default: 0 },
    { key: "is_active", label: "Active", type: "checkbox", default: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Buffer slot ---------- */
const buffersPage = new CrudPage({
  endpoint: "/api/buffer-slots/", pk: "slot_id", title: "Buffer", listEl: "crud-buffers",
  searchKeys: ["slot_id", "name", "rssid", "pallet_code", "product_model"],
  columns: [
    { label: "Slot", key: "slot_id", mono: true },
    { label: "Name", key: "name" },
    { label: "Type", key: "buffer_type" },
    { label: "Node", key: "node_id", mono: true },
    { label: "Status", key: "slot_status", badge: true, map: STATUS.slot },
  ],
  fields: [
    { key: "slot_id", label: "Slot ID", type: "text", required: true },
    { key: "name", label: "Name", type: "text" },
    { key: "buffer_type", label: "Buffer type", type: "select", options: BUFFER_TYPES, default: "STORAGE" },
    { key: "zone", label: "Buffer Zone", type: "select", required: true, options: bufferZoneOptions },
    { key: "node", label: "Node (1-1)", type: "select", required: true, options: nodeOptions },
    { key: "product_model", label: "Product code", type: "text" },
    { key: "process", label: "Process", type: "text" },
    { key: "enable", label: "Active", type: "checkbox", default: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Topology ---------- */
const topologyPage = new CrudPage({
  endpoint: "/api/topology/", pk: "map_id", title: "Topology", listEl: "crud-topology",
  searchKeys: ["map_id", "map_name"],
  columns: [
    { label: "Map ID", key: "map_id", mono: true },
    { label: "Name", key: "map_name" },
    { label: "Version", key: "version", mono: true },
    { label: "Status", key: "is_active", render: (r) => r.is_active ? '<span class="badge tone-green">Active</span>' : '<span class="badge tone-gray">Inactive</span>' },
  ],
  fields: [
    { key: "map_id", label: "Map ID", type: "text", required: true, placeholder: "MAP_01" },
    { key: "map_name", label: "Map name", type: "text", required: true },
    { key: "version", label: "Version", type: "text", default: "1.0.0" },
    { key: "is_active", label: "Active", type: "checkbox", default: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Action ---------- */
const actionsPage = new CrudPage({
  endpoint: "/api/actions/", pk: "id", title: "Action", listEl: "crud-actions",
  searchKeys: ["action_type", "description"],
  columns: [
    { label: "Action type", key: "action_type", mono: true },
    { label: "Blocking", key: "blocking_type", render: (r) => `<span class="badge tone-amber">${esc(r.blocking_type)}</span>` },
    { label: "Description", key: "description" },
  ],
  fields: [
    { key: "action_type", label: "Action type", type: "text", required: true, placeholder: "pick / drop / charge" },
    { key: "blocking_type", label: "Blocking", type: "select", options: BLOCKING, default: "HARD" },
    { key: "description", label: "Description", type: "text", full: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: MapBase ---------- */
const shortId = (v) => (v ? String(v).slice(0, 8) : "—");
const mapBasePage = new CrudPage({
  endpoint: "/api/maps/", pk: "id", title: "MapBase", listEl: "crud-mapbase",
  searchKeys: ["name", "version", "part"],
  filters: [{ key: "status", label: "Status", options: MAP_STATUS }],
  columns: [
    { label: "Name", key: "name" },
    { label: "Topology", key: "topology", mono: true },
    { label: "Version", key: "version", mono: true },
    { label: "Part", key: "part" },
    { label: "Status", key: "status", badge: true, map: STATUS.mapstatus },
  ],
  fields: [
    { key: "name", label: "Map name", type: "text", required: true },
    { key: "topology", label: "Topology", type: "select", options: topologyOptions },
    { key: "version", label: "Version", type: "text", default: "1.0.0" },
    { key: "part", label: "Part", type: "text" },
    { key: "status", label: "Status", type: "select", options: MAP_STATUS, default: "DRAFT" },
    { key: "description", label: "Description", type: "text", full: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: ZoneSet ---------- */
const zoneSetsPage = new CrudPage({
  endpoint: "/api/zone-sets/", pk: "id", title: "ZoneSet", listEl: "crud-zonesets",
  searchKeys: ["name", "version"],
  columns: [
    { label: "Name", key: "name" },
    { label: "Map", key: "map", render: (r) => shortId(r.map), mono: true },
    { label: "Version", key: "version", mono: true },
    { label: "Status", key: "is_active", render: (r) => r.is_active ? '<span class="badge tone-green">On</span>' : '<span class="badge tone-gray">Off</span>' },
  ],
  fields: [
    { key: "name", label: "ZoneSet name", type: "text", required: true },
    { key: "map", label: "MapBase", type: "select", required: true, options: mapBaseOptions },
    { key: "version", label: "Version", type: "text", default: "1.0.0" },
    { key: "description", label: "Description", type: "text", full: true },
    { key: "is_active", label: "Active", type: "checkbox", default: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: TrafficZone ---------- */
const trafficZonesPage = new CrudPage({
  endpoint: "/api/traffic-zones/", pk: "id", title: "TrafficZone", listEl: "crud-trafficzones",
  searchKeys: ["name", "zone_type"],
  columns: [
    { label: "Name", key: "name" },
    { label: "Type", key: "zone_type", render: (r) => `<span class="badge tone-teal">${esc(r.zone_type)}</span>` },
    { label: "Capacity", key: "max_occupancy", num: true },
    { label: "Priority", key: "priority", num: true },
    { label: "Speed", key: "max_speed", num: true, render: (r) => fmtNum(r.max_speed) },
  ],
  fields: [
    { key: "name", label: "Zone name", type: "text", required: true },
    { key: "zone_type", label: "Zone type", type: "select", options: TRAFFIC_ZONE_TYPES, default: "LINE" },
    { key: "zone_set", label: "ZoneSet", type: "select", required: true, options: zoneSetOptions },
    { key: "map", label: "MapBase", type: "select", required: true, options: mapBaseOptions },
    { key: "max_occupancy", label: "Max occupancy", type: "number", default: 1 },
    { key: "priority", label: "Priority", type: "number", default: 0 },
    { key: "max_speed", label: "Max speed", type: "number", default: 1.2 },
    { key: "description", label: "Description", type: "text", full: true },
  ],
  onChanged: reloadMap,
});

/* ---------- CRUD: Schedule ---------- */
const schedulesPage = new CrudPage({
  endpoint: "/api/schedules/", pk: "id", title: "Schedule", listEl: "crud-schedules",
  searchKeys: ["name"],
  columns: [
    { label: "Name", key: "name" },
    { label: "AMRs needed", key: "number_of_amr_need", num: true },
    { label: "Active orders", key: "number_of_active_order", num: true },
    { label: "Status", key: "enable", render: (r) => r.enable ? '<span class="badge tone-green">On</span>' : '<span class="badge tone-gray">Off</span>' },
  ],
  fields: [
    { key: "name", label: "Schedule name", type: "text", required: true },
    { key: "description", label: "Description", type: "text", full: true },
    { key: "map", label: "MapBase", type: "select", options: mapBaseOptions },
    { key: "number_of_amr_need", label: "AMRs needed", type: "number", default: 1 },
    { key: "enable", label: "Active", type: "checkbox", default: true },
  ],
  onChanged: reloadMap,
});

/* ---------- Add node / edge visually ---------- */
function addNodeAt(x, y) {
  engine.setMode("pan");
  const opts = NODE_TYPES.map((o) => `<option value="${o.v}">${o.l}</option>`).join("");
  openModal(`<h2>Add node at (${x.toFixed(1)}, ${y.toFixed(1)})</h2>
    <div class="form-grid">
      <div class="field"><label>Node ID *</label><input id="an-id" placeholder="N_XX"></div>
      <div class="field"><label>Type</label><select id="an-type">${opts}</select></div>
      <div class="field full"><label>Name</label><input id="an-name" placeholder="Node name"></div>
    </div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="submitAddNode(${x}, ${y})">Create</button></div>`);
}
async function submitAddNode(x, y) {
  const node_id = $("an-id").value.trim();
  if (!node_id) { toast("Missing Node ID", "error"); return; }
  try {
    const ts = await api.get("/api/topology/");
    const active = ts.find((t) => t.is_active) || ts[0];
    await api.post("/api/nodes/", { node_id, map_topology: active.map_id, node_type: $("an-type").value, name: $("an-name").value, pos_x: x, pos_y: y });
    closeModal(); toast(`Added ${node_id}`, "ok"); reloadMap(); nodesPage.load();
  } catch (e) { toast("Error: " + e.message, "error"); }
}

function addEdgeBetween(a, b) {
  engine.setMode("pan");
  const na = engine.nodesById.get(a), nb = engine.nodesById.get(b);
  const dist = Math.hypot(nb.pos_x - na.pos_x, nb.pos_y - na.pos_y);
  openModal(`<h2>Add edge ${esc(a)} → ${esc(b)}</h2>
    <div class="form-grid">
      <div class="field full"><label>Edge ID *</label><input id="ae-id" placeholder="E_XX"></div>
      <div class="field"><label>Direction</label><select id="ae-dir"><option value="UNI">One-way</option><option value="BI">Two-way</option></select></div>
      <div class="field"><label>Distance (m)</label><input id="ae-dist" type="number" step="any" value="${dist.toFixed(2)}"></div>
    </div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="submitAddEdge('${esc(a)}','${esc(b)}')">Create</button></div>`);
}
async function submitAddEdge(a, b) {
  const edge_id = $("ae-id").value.trim();
  if (!edge_id) { toast("Missing Edge ID", "error"); return; }
  try {
    await api.post("/api/edges/", { edge_id, start_node: a, end_node: b, direction: $("ae-dir").value, distance: parseFloat($("ae-dist").value) || 0 });
    closeModal(); toast(`Added ${edge_id}`, "ok"); reloadMap(); edgesPage.load();
  } catch (e) { toast("Error: " + e.message, "error"); }
}

/* ---------- Import map (JSON) ---------- */
function openImportMap() {
  openModal(`<h2>Import map (JSON)</h2>
    <div class="field" style="margin-bottom:12px">
      <label>Choose a .json file</label>
      <input type="file" id="imp-file" accept=".json,application/json">
    </div>
    <div class="field">
      <label>Or paste JSON (topology / nodes / edges)</label>
      <textarea id="imp-text" style="min-height:140px" placeholder='{"map_name":"MAP_01","nodes":[{"node_id":"N_01","node_type":"WAYPOINT","pos_x":1.0,"pos_y":2.0}],"edges":[{"edge_id":"E_01","start_node":"N_01","end_node":"N_02","distance":2.5,"direction":"UNI"}]}'></textarea>
    </div>
    <div class="hint" style="margin-top:8px">
      Format: <code>{ map_id?, map_name, version?, nodes:[...], edges:[...], zones?:[...] }</code>.
      You can paste the output of <code>GET /api/topology/full_map/</code>. Records with matching ids are updated (upsert).
    </div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="submitImportMap()">Import</button></div>`);
}
async function submitImportMap() {
  const fileInput = $("imp-file");
  const file = fileInput && fileInput.files && fileInput.files[0];
  const text = ($("imp-text").value || "").trim();
  try {
    let res;
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      res = await request("POST", "/api/topology/import_map/", fd);
    } else {
      if (!text) { toast("Choose a file or paste JSON", "error"); return; }
      res = await api.post("/api/topology/import_map/", JSON.parse(text));
    }
    closeModal();
    toast(`Import done: ${res.nodes.created} new nodes, ${res.edges.created} new edges, ${res.zones.created} new zones (map: ${res.map_id})`, "ok");
    reloadMap();
    await Promise.allSettled([nodesPage.load(), edgesPage.load(), zonesPage.load(), topologyPage.load()]);
  } catch (e) { toast("Import error: " + e.message, "error"); }
}

/* ---------- Tabs & buttons ---------- */
function wireTabs() {
  document.querySelectorAll("#entity-tabs .tab-btn").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#entity-tabs .tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll("#entity-tabs ~ .tab-pane, .tab-pane").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const pane = document.getElementById("tab-" + b.dataset.tab);
      if (pane) pane.classList.add("active");
    };
  });
}
function setHint(mode) {
  const hints = {
    pan: "View mode — drag to pan, drag a node to adjust its position",
    "add-node": "Click the map to place a new node",
    connect: "Click the first node, then the second node to create an edge",
  };
  els.hint.textContent = hints[mode] || hints.pan;
}
function wireButtons() {
  $("btn-zoom-in").onclick = () => engine.zoomBy(1.25);
  $("btn-zoom-out").onclick = () => engine.zoomBy(1 / 1.25);
  $("btn-fit").onclick = () => engine.fit();
  $("btn-grid").onclick = () => { engine.showGrid = !engine.showGrid; };
  $("btn-import").onclick = openImportMap;
  $("btn-add-node").onclick = () => { const m = engine.mode === "add-node" ? "pan" : "add-node"; engine.setMode(m); setHint(m); $("btn-add-node").classList.toggle("active", m === "add-node"); $("btn-connect").classList.remove("active"); };
  $("btn-connect").onclick = () => { const m = engine.mode === "connect" ? "pan" : "connect"; engine.setMode(m); setHint(m); $("btn-connect").classList.toggle("active", m === "connect"); $("btn-add-node").classList.remove("active"); };
  window.addEventListener("resize", () => engine.resize());
}

async function init() {
  wireTabs(); wireButtons();
  setHint("pan");
  await reloadMap();
  await Promise.allSettled([nodesPage.load(), edgesPage.load(), zonesPage.load(), buffersPage.load(), topologyPage.load(), actionsPage.load(), mapBasePage.load(), zoneSetsPage.load(), trafficZonesPage.load(), schedulesPage.load()]);
  engine.fit();
}
init();


