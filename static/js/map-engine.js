/* ==========================================================================
   ACS · MapEngine — canvas renderer for the topology map (view + edit).
   Shared by Dashboard (monitoring) and Map Editor (visual CRUD).
   ========================================================================== */
"use strict";

const NODE_COLORS = {
  WAYPOINT: "--accent", INTERSECTION: "--info", BUFFER: "--ok",
  CHARGER: "--warn", PARKING: "--muted", LINE: "--accent-2",
};

class MapEngine {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.scale = 8;                 // px / mét
    this.ox = 0; this.oy = 0;
    this.bounds = null;
    this.showGrid = true;
    this.mode = "pan";              // pan | select | add-node | connect
    this.editable = !!opts.editable;

    this.nodes = []; this.nodesById = new Map();
    this.edges = []; this.zones = []; this.bufferSlots = [];
    this.robots = new Map();        // serial -> snapshot + interpolation

    this.selected = null;           // {kind, id}
    this._drag = null;
    this._connectFrom = null;

    this.onPick = opts.onPick || null;
    this.onAddNode = opts.onAddNode || null;
    this.onConnect = opts.onConnect || null;
    this.onMoveNode = opts.onMoveNode || null;
    this.onSelect = opts.onSelect || null;
    this.onCursor = opts.onCursor || null;

    this._bind();
    this.resize();
    this._loop();
  }

  setMap(data) {
    this.nodes = data.nodes || [];
    this.nodesById = new Map(this.nodes.map((n) => [n.node_id, n]));
    this.edges = data.edges || [];
    this.zones = data.zones || [];
    this.bufferSlots = data.bufferSlots || [];
    if (this.nodes.length) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of this.nodes) {
        minX = Math.min(minX, n.pos_x); maxX = Math.max(maxX, n.pos_x);
        minY = Math.min(minY, n.pos_y); maxY = Math.max(maxY, n.pos_y);
      }
      this.bounds = { minX, minY, maxX, maxY };
    } else {
      this.bounds = { minX: -5, minY: -5, maxX: 5, maxY: 5 };
    }
  }

  setRobots(list) {
    for (const r of list || []) {
      const d = this.robots.get(r.serial_number) || {};
      d.targetX = r.pos_x; d.targetY = r.pos_y; d.targetTheta = r.theta || 0;
      if (d.x == null) { d.x = d.targetX; d.y = d.targetY; d.theta = d.targetTheta; }
      Object.assign(d, r);
      this.robots.set(r.serial_number, d);
    }
  }

  setMode(m) { this.mode = m; this._connectFrom = null; }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.floor(r.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = r.width; this.H = r.height;
  }
  worldToScreen(x, y) { return { x: x * this.scale + this.ox, y: -y * this.scale + this.oy }; }
  screenToWorld(x, y) { return { x: (x - this.ox) / this.scale, y: (y - this.oy) / -this.scale }; }
  fit() {
    if (!this.bounds) return;
    const b = this.bounds;
    const w = (b.maxX - b.minX) || 10, h = (b.maxY - b.minY) || 10;
    const pad = 60;
    this.scale = Math.max(0.5, Math.min(200, Math.min((this.W - pad * 2) / w, (this.H - pad * 2) / h)));
    this.ox = (this.W - (b.minX + b.maxX) * this.scale) / 2;
    this.oy = (this.H + (b.minY + b.maxY) * this.scale) / 2;
  }
  zoomBy(f) {
    const w = this.screenToWorld(this.W / 2, this.H / 2);
    this.scale = Math.max(0.5, Math.min(200, this.scale * f));
    this.ox = this.W / 2 - w.x * this.scale;
    this.oy = this.H / 2 + w.y * this.scale;
  }

  _loop() {
    const k = 0.16;
    for (const d of this.robots.values()) {
      d.x += (d.targetX - d.x) * k;
      d.y += (d.targetY - d.y) * k;
      let dt = d.targetTheta - d.theta;
      while (dt > Math.PI) dt -= Math.PI * 2;
      while (dt < -Math.PI) dt += Math.PI * 2;
      d.theta += dt * k;
    }
    this.render();
    requestAnimationFrame(() => this._loop());
  }

  _bind() {
    const c = this.canvas;
    c.addEventListener("mousedown", (e) => this._onDown(e));
    window.addEventListener("mousemove", (e) => this._onMove(e));
    window.addEventListener("mouseup", (e) => this._onUp(e));
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      const sx = e.clientX - r.left, sy = e.clientY - r.top;
      const w = this.screenToWorld(sx, sy);
      this.scale = Math.max(0.5, Math.min(200, this.scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      this.ox = sx - w.x * this.scale;
      this.oy = sy + w.y * this.scale;
    }, { passive: false });
    window.addEventListener("resize", () => this.resize());
  }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return { sx: e.clientX - r.left, sy: e.clientY - r.top };
  }

  _onDown(e) {
    const { sx, sy } = this._pos(e);
    const w = this.screenToWorld(sx, sy);
    const hit = this._hitTest(w, 12 / this.scale);
    if (this.mode === "add-node") {
      if (this.onAddNode) this.onAddNode(w.x, w.y);
      return;
    }
    if (this.mode === "connect") {
      if (hit && hit.kind === "node") {
        if (!this._connectFrom) { this._connectFrom = hit.id; this.selected = hit; }
        else {
          if (this.onConnect) this.onConnect(this._connectFrom, hit.id);
          this._connectFrom = null;
        }
      } else { this._connectFrom = null; }
      return;
    }
    if (hit && hit.kind === "node") {
      this._drag = { kind: "node", id: hit.id, x: e.clientX, y: e.clientY, moved: false };
    } else {
      this._drag = { kind: "pan", x: e.clientX, y: e.clientY, moved: false };
    }
    this.selected = hit;
    if (this.onSelect) this.onSelect(hit);
  }

  _onMove(e) {
    const r = this.canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (this._drag) {
      const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) this._drag.moved = true;
      if (this._drag.kind === "pan") {
        this.ox += dx; this.oy += dy;
      } else if (this._drag.kind === "node" && this.editable) {
        const w = this.screenToWorld(sx, sy);
        const node = this.nodesById.get(this._drag.id);
        if (node) { node.pos_x = w.x; node.pos_y = w.y; }
      }
      this._drag.x = e.clientX; this._drag.y = e.clientY;
    }
    if (this.onCursor) this.onCursor(this.screenToWorld(sx, sy));
  }

  _onUp() {
    if (this._drag && this._drag.kind === "node" && this._drag.moved && this.onMoveNode) {
      const node = this.nodesById.get(this._drag.id);
      this.onMoveNode(this._drag.id, node.pos_x, node.pos_y);
    }
    this._drag = null;
  }

  _hitTest(w, tol) {
    let best = null, bestD = tol;
    for (const [serial, d] of this.robots) {
      const dist = Math.hypot(d.x - w.x, d.y - w.y);
      if (dist < bestD) { bestD = dist; best = { kind: "robot", id: serial, x: d.x, y: d.y }; }
    }
    if (best) return best;
    bestD = tol;
    for (const n of this.nodes) {
      const dist = Math.hypot(n.pos_x - w.x, n.pos_y - w.y);
      if (dist < bestD) { bestD = dist; best = { kind: "node", id: n.node_id, x: n.pos_x, y: n.pos_y }; }
    }
    return best;
  }

  /* ---------- drawing ---------- */
  _niceStep(raw) {
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    if (norm < 1.5) return mag;
    if (norm < 3) return 2 * mag;
    if (norm < 7) return 5 * mag;
    return 10 * mag;
  }

  render() {
    const c = this.ctx;
    c.clearRect(0, 0, this.W, this.H);
    c.fillStyle = cssVar("--bg");
    c.fillRect(0, 0, this.W, this.H);
    this._drawGrid();
    this._drawZones();
    this._drawEdges();
    this._drawNodes();
    this._drawBuffers();
    this._drawRobots();
  }

  _drawGrid() {
    if (!this.showGrid) return;
    const c = this.ctx;
    const step = this._niceStep(80 / this.scale);
    const w0 = this.screenToWorld(0, 0), w1 = this.screenToWorld(this.W, this.H);
    const minX = Math.min(w0.x, w1.x), maxX = Math.max(w0.x, w1.x);
    const minY = Math.min(w0.y, w1.y), maxY = Math.max(w0.y, w1.y);
    c.save();
    c.strokeStyle = cssVar("--border");
    c.lineWidth = 1; c.globalAlpha = 0.5;
    for (let x = Math.floor(minX / step) * step; x <= maxX; x += step) {
      const p = this.worldToScreen(x, 0);
      c.beginPath(); c.moveTo(p.x, 0); c.lineTo(p.x, this.H); c.stroke();
    }
    for (let y = Math.floor(minY / step) * step; y <= maxY; y += step) {
      const p = this.worldToScreen(0, y);
      c.beginPath(); c.moveTo(0, p.y); c.lineTo(this.W, p.y); c.stroke();
    }
    c.restore();
  }

  _drawZones() {
    const c = this.ctx;
    for (const z of this.zones) {
      const a = this.worldToScreen(z.pos_x - z.size_x / 2, z.pos_y + z.size_y / 2);
      const b = this.worldToScreen(z.pos_x + z.size_x / 2, z.pos_y - z.size_y / 2);
      c.save();
      c.fillStyle = cssVar("--accent-2"); c.globalAlpha = 0.06;
      c.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
      c.strokeStyle = cssVar("--accent-2"); c.globalAlpha = 0.5;
      c.setLineDash([4, 3]);
      c.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
      c.restore();
    }
  }

  _drawEdges() {
    const c = this.ctx;
    for (const e of this.edges) {
      const s = this.nodesById.get(e.start_node), t = this.nodesById.get(e.end_node);
      if (!s || !t) continue;
      const p1 = this.worldToScreen(s.pos_x, s.pos_y);
      const p2 = this.worldToScreen(t.pos_x, t.pos_y);
      const sel = this.selected && this.selected.kind === "edge" && this.selected.id === e.edge_id;
      c.save();
      c.strokeStyle = e.is_locked ? cssVar("--warn") : (sel ? cssVar("--accent") : cssVar("--border-strong"));
      c.lineWidth = sel ? 2.5 : 1.5;
      c.beginPath(); c.moveTo(p1.x, p1.y); c.lineTo(p2.x, p2.y); c.stroke();
      if (e.direction === "UNI") this._arrow(p1, p2, c.strokeStyle);
      c.restore();
    }
  }

  _arrow(p1, p2, color) {
    const c = this.ctx;
    const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
    const ang = Math.atan2(p2.y - p1.y, p2.x - p1.x);
    const s = 7;
    c.save();
    c.fillStyle = color;
    c.beginPath();
    c.moveTo(mx + s * Math.cos(ang), my + s * Math.sin(ang));
    c.lineTo(mx + s * Math.cos(ang + 2.4), my + s * Math.sin(ang + 2.4));
    c.lineTo(mx + s * Math.cos(ang - 2.4), my + s * Math.sin(ang - 2.4));
    c.closePath(); c.fill();
    c.restore();
  }

  _drawNodes() {
    const c = this.ctx;
    for (const n of this.nodes) {
      const p = this.worldToScreen(n.pos_x, n.pos_y);
      const color = cssVar(NODE_COLORS[n.node_type] || "--muted");
      const sel = this.selected && this.selected.kind === "node" && this.selected.id === n.node_id;
      const r = sel ? 7 : 5;
      c.save();
      if (sel) {
        c.beginPath(); c.arc(p.x, p.y, r + 5, 0, Math.PI * 2);
        c.strokeStyle = cssVar("--accent"); c.lineWidth = 1.5; c.stroke();
      }
      c.beginPath(); c.arc(p.x, p.y, r, 0, Math.PI * 2);
      c.fillStyle = color; c.fill();
      c.strokeStyle = cssVar("--bg"); c.lineWidth = 1.5; c.stroke();
      if (n.is_init) {
        c.beginPath(); c.arc(p.x, p.y, r + 2, 0, Math.PI * 2);
        c.strokeStyle = cssVar("--warn"); c.lineWidth = 1; c.stroke();
      }
      if (n.is_occupied) {
        c.beginPath(); c.arc(p.x, p.y, r + 8, 0, Math.PI * 2);
        c.strokeStyle = cssVar("--danger"); c.lineWidth = 1; c.stroke();
      }
      if (this.scale >= 6) {
        c.fillStyle = cssVar("--text-2");
        c.font = "10px " + cssVar("--font-mono");
        c.fillText(n.node_id, p.x + r + 3, p.y - r - 2);
      }
      c.restore();
    }
  }

  _drawBuffers() {
    const c = this.ctx;
    for (const s of this.bufferSlots) {
      const n = this.nodesById.get(s.node_id);
      if (!n) continue;
      const p = this.worldToScreen(n.pos_x, n.pos_y);
      const color = s.slot_status === "OCCUPIED" ? cssVar("--ok") : s.slot_status === "RESERVED" ? cssVar("--warn") : cssVar("--muted");
      c.save();
      c.fillStyle = color; c.globalAlpha = 0.9;
      c.fillRect(p.x - 4, p.y - 14, 8, 6);
      c.restore();
    }
  }

  _drawRobots() {
    const c = this.ctx;
    const map = { INIT: "--state-idle", IDLE: "--state-idle", RUNNING: "--state-running", BLOCKED: "--state-blocked", WARNING: "--state-warning", ERROR: "--state-error", OFFLINE: "--state-offline" };
    for (const d of this.robots.values()) {
      const p = this.worldToScreen(d.x, d.y);
      const color = cssVar(map[d.operational_state] || "--muted");
      c.save();
      c.translate(p.x, p.y);
      c.rotate(-d.theta);
      c.beginPath();
      c.moveTo(8, 0); c.lineTo(-5, -5); c.lineTo(-2, 0); c.lineTo(-5, 5);
      c.closePath();
      c.fillStyle = color; c.fill();
      c.restore();
      const batt = Math.max(0, Math.min(100, d.battery_level || 0));
      const bw = 16, bh = 3.5;
      c.save();
      c.fillStyle = "rgba(0,0,0,.55)";
      c.fillRect(p.x - bw / 2, p.y + 10, bw, bh);
      c.fillStyle = batt > 50 ? cssVar("--ok") : batt > 25 ? cssVar("--warn") : cssVar("--danger");
      c.fillRect(p.x - bw / 2, p.y + 10, bw * batt / 100, bh);
      c.fillStyle = cssVar("--text");
      c.font = "10px " + cssVar("--font-mono");
      c.fillText(d.serial_number, p.x + 9, p.y - 7);
      c.restore();
    }
  }

  legendHtml() {
    const items = [
      ["Node", NODE_COLORS.WAYPOINT], ["Intersection", NODE_COLORS.INTERSECTION],
      ["Buffer", NODE_COLORS.BUFFER], ["Charging", NODE_COLORS.CHARGER],
      ["AMR", "--state-running"], ["Zone", "--accent-2"],
    ];
    return `<h3>Legend</h3>` + items.map(([l, v]) => `<div class="lg"><span class="sw" style="background:${cssVar(v)}"></span>${l}</div>`).join("");
  }
}



