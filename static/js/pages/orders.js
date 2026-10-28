/* ACS · Orders — orders, supply, OrderMaster, SubOrder management */
"use strict";

const ORDER_TYPES = [
  { v: "TRANSPORT", l: "Transport" }, { v: "TRANSFER", l: "Buffer Transfer" },
  { v: "CHARGE", l: "Charging" }, { v: "PARK", l: "Parking" }, { v: "RETURN", l: "Return Home" },
];
const ORDER_STATUS = ["CREATED", "ASSIGNED", "EXECUTING", "COMPLETED", "CANCELLED", "FAILED"].map((v) => ({ v, l: v }));
const SUPPLY_SOURCES = [{ v: "MANUAL", l: "Manual" }, { v: "ALIGO", l: "Aligo MES" }];

/* ---------- New transport order modal ---------- */
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
  try { const o = await api.post("/api/orders/", body); closeModal(); toast(`Created ${o.order_id}`, "ok"); ordersPage.load(); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

async function cancelOrder(o) {
  if (!await confirmDialog("Cancel order", `Cancel <b>${esc(o.order_id)}</b>?`, "Cancel order")) return;
  try { await api.post(`/api/orders/${encodeURIComponent(o.order_id)}/cancel/`, {}); toast("Cancelled", "ok"); ordersPage.load(); }
  catch (e) { toast("Error: " + e.message, "error"); }
}
async function resendOrder(o) {
  try { await api.post(`/api/orders/${encodeURIComponent(o.order_id)}/resend/`, {}); toast("Resent", "ok"); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

/* ---------- CRUD: Orders ---------- */
const ordersPage = new CrudPage({
  endpoint: "/api/orders/", pk: "order_id", title: "order", listEl: "crud-orders",
  canCreate: false, canDelete: true,
  toolbar: (bar) => {
    const b = el("button", { class: "btn btn-primary btn-sm", html: "＋ Transport Order" });
    b.addEventListener("click", openNewOrder);
    bar.insertBefore(b, bar.querySelector(".spacer"));
  },
  searchKeys: ["order_id", "robot_serial", "src_slot", "dst_slot"],
  filters: [{ key: "order_status", label: "Status", options: ORDER_STATUS }],
  columns: [
    { label: "Order ID", key: "order_id", mono: true },
    { label: "Type", key: "order_type" },
    { label: "AMR", key: "robot_serial", mono: true },
    { label: "Route", render: (r) => `${esc(r.src_slot)} → ${esc(r.dst_slot)}` },
    { label: "Status", key: "order_status", badge: true, map: STATUS.order },
    { label: "Priority", key: "priority", num: true },
    { label: "Created", key: "created_at", render: (r) => fmtTime(r.created_at), mono: true },
  ],
  fields: [
    { key: "order_type", label: "Order type", type: "select", options: ORDER_TYPES },
    { key: "priority", label: "Priority", type: "number" },
    { key: "order_status", label: "Status", type: "select", options: ORDER_STATUS },
    { key: "failure_reason", label: "Failure reason", type: "text", full: true },
  ],
  customActions: [
    { label: "Resend", handler: resendOrder, visible: (r) => !["COMPLETED", "CANCELLED", "FAILED"].includes(r.order_status) && r.robot },
    { label: "Cancel", cls: "btn-danger", handler: cancelOrder, visible: (r) => ["CREATED", "ASSIGNED", "EXECUTING"].includes(r.order_status) },
  ],
});

/* ---------- New supply request modal ---------- */
function openNewSupply() {
  openModal(`<h2>Supply request</h2>
    <div class="form-grid">
      <div class="field"><label>Line</label><input id="s-line" placeholder="LINE_01"></div>
      <div class="field"><label>Product</label><input id="s-model" placeholder="MODEL_X"></div>
      <div class="field"><label>Priority (10–100)</label><input id="s-prio" type="number" min="10" max="100" value="50"></div>
      <div class="field"><label>Quantity</label><input id="s-qty" type="number" min="1" value="1"></div>
    </div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="submitNewSupply()">Create request</button></div>`);
}
async function submitNewSupply() {
  const body = { source: "MANUAL", line_id: $("s-line").value.trim(), product_model: $("s-model").value.trim(), priority: parseInt($("s-prio").value || "50", 10), quantity: parseInt($("s-qty").value || "1", 10) };
  try { const s = await api.post("/api/supply-requests/", body); closeModal(); toast(`Created ${s.request_id}`, "ok"); supplyPage.load(); }
  catch (e) { toast("Error: " + e.message, "error"); }
}

/* ---------- CRUD: Supply ---------- */
const supplyPage = new CrudPage({
  endpoint: "/api/supply-requests/", pk: "id", title: "request", listEl: "crud-supply",
  canCreate: false, canDelete: false,
  toolbar: (bar) => {
    const b = el("button", { class: "btn btn-primary btn-sm", html: "＋ Supply Request" });
    b.addEventListener("click", openNewSupply);
    bar.insertBefore(b, bar.querySelector(".spacer"));
  },
  searchKeys: ["request_id", "line_id", "product_model"],
  filters: [{ key: "status", label: "Status", options: Object.entries(STATUS.supply).map(([v, m]) => ({ v, l: m.label })) }],
  columns: [
    { label: "Request ID", key: "request_id", mono: true },
    { label: "Source", key: "source" },
    { label: "Line", key: "line_id", mono: true },
    { label: "Product", key: "product_model" },
    { label: "Qty", key: "quantity", num: true },
    { label: "Status", key: "status", badge: true, map: STATUS.supply },
    { label: "Created", key: "created_at", render: (r) => fmtTime(r.created_at), mono: true },
  ],
  fields: [
    { key: "source", label: "Source", type: "select", options: SUPPLY_SOURCES },
    { key: "line_id", label: "Line", type: "text" },
    { key: "product_model", label: "Product", type: "text" },
    { key: "process", label: "Process", type: "text" },
    { key: "quantity", label: "Quantity", type: "number" },
    { key: "priority", label: "Priority", type: "number" },
  ],
  customActions: [
    { label: "Cancel", cls: "btn-danger", handler: async (s) => { if (await confirmDialog("Cancel request", `Cancel <b>${esc(s.request_id)}</b>?`, "Cancel")) { try { await api.post(`/api/supply-requests/${s.id}/cancel/`, {}); toast("Cancelled", "ok"); supplyPage.load(); } catch (e) { toast("Error: " + e.message, "error"); } } }, visible: (s) => ["PENDING", "PAIRED", "IN_PROGRESS"].includes(s.status) },
  ],
});

/* ---------- OrderMaster & SubOrder (read-only + actions) ---------- */
async function loadMasters() {
  const rows = await api.get("/api/order-masters/");
  renderTable($("crud-masters"), {
    columns: [
      { label: "Master ID", key: "master_id", mono: true },
      { label: "Status", key: "status", badge: true, map: STATUS.master },
      { label: "Priority", key: "priority", num: true },
      { label: "Sub-orders", render: (r) => (r.sub_orders || []).length, num: true },
      { label: "Created", key: "created_at", render: (r) => fmtTime(r.created_at), mono: true },
    ],
    rows,
    actions: [
      { label: "Plan", handler: async (m) => { try { await api.post(`/api/order-masters/${m.id}/plan/`, {}); toast("Planned", "ok"); loadMasters(); } catch (e) { toast("Error: " + e.message, "error"); } }, visible: (m) => m.status === "NEW" },
      { label: "Cancel", cls: "btn-danger", handler: async (m) => { if (await confirmDialog("Cancel master", `Cancel <b>${esc(m.master_id)}</b>?`, "Cancel")) { try { await api.post(`/api/order-masters/${m.id}/cancel/`, {}); toast("Cancelled", "ok"); loadMasters(); } catch (e) { toast("Error: " + e.message, "error"); } } }, visible: (m) => !["COMPLETED", "CANCELLED", "FAILED"].includes(m.status) },
    ],
    empty: "No OrderMasters",
  });
}
async function loadSubOrders() {
  const rows = await api.get("/api/sub-orders/");
  renderTable($("crud-suborders"), {
    columns: [
      { label: "Sub ID", key: "sub_order_id", mono: true },
      { label: "Master", key: "master_id", mono: true },
      { label: "AMR", key: "amr_serial", mono: true },
      { label: "Status", key: "status", badge: true, map: STATUS.suborder },
      { label: "Resends", key: "resend_count", num: true },
      { label: "Updated", key: "updated_at", render: (r) => fmtTime(r.updated_at), mono: true },
    ],
    rows,
    actions: [
      { label: "Resend", handler: async (s) => { try { await api.post(`/api/sub-orders/${s.id}/resend/`, {}); toast("Resent", "ok"); } catch (e) { toast("Error: " + e.message, "error"); } }, visible: (s) => ["PENDING", "SENT", "ACKED"].includes(s.status) },
    ],
    empty: "No Sub-Orders",
  });
}

/* ---------- Tabs & init ---------- */
function wireTabs() {
  document.querySelectorAll("#order-tabs .tab-btn").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#order-tabs .tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll("#order-tabs + .tab-pane, .tab-pane").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const pane = document.getElementById("tab-" + b.dataset.tab);
      if (pane) pane.classList.add("active");
    };
  });
}
async function init() {
  wireTabs();
  await Promise.allSettled([ordersPage.load(), supplyPage.load(), loadMasters(), loadSubOrders()]);
  setInterval(() => ordersPage.load(), 8000);
}
init();

