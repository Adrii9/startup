/* Force-directed graph on a plain canvas, 2D and 3D from the same simulation.
   No library: the whole thing is a spring layout, a rotation matrix and a
   perspective divide, and keeping it ours means the look matches the rest of
   the shell instead of a chart library's defaults. */

const FOV = 950;

class GraphView {
  constructor(canvas, onSelect) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.onSelect = onSelect;

    this.nodes = [];
    this.links = [];
    this.byId = new Map();

    this.dim = 2;
    this.alpha = 1;
    this.rx = -0.35; this.ry = 0.6;
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this.selected = null;
    this.hover = null;
    this.highlight = null;      // Set of ids, or null for "everything"
    this.spin = true;           // idle rotation, until the user takes over
    this.running = false;

    this._bind();
  }

  // ── data ──────────────────────────────────────────────────────────
  setData(g) {
    const keep = new Map(this.nodes.map(n => [n.id, n]));
    this.nodes = g.nodes.map(n => {
      const old = keep.get(n.id);
      return Object.assign({
        x: old ? old.x : (Math.random() - 0.5) * 240,
        y: old ? old.y : (Math.random() - 0.5) * 240,
        z: old ? old.z : (Math.random() - 0.5) * 240,
        vx: 0, vy: 0, vz: 0
      }, n);
    });
    this.byId = new Map(this.nodes.map(n => [n.id, n]));
    this.links = g.links.map(l => ({ s: this.byId.get(l.s), t: this.byId.get(l.t), kind: l.kind }))
                        .filter(l => l.s && l.t);

    this.neighbours = new Map(this.nodes.map(n => [n.id, new Set()]));
    for (const l of this.links) {
      this.neighbours.get(l.s.id).add(l.t.id);
      this.neighbours.get(l.t.id).add(l.s.id);
    }
    for (const n of this.nodes) n.deg = this.neighbours.get(n.id).size;
    this.alpha = 1;
    if (this.selected && !this.byId.has(this.selected)) this.selected = null;

    // Settle before the first paint: an audience should see a graph, not an
    // explosion that slowly turns into one.
    for (let i = 0; i < 260; i++) this.tick();
    this.alpha = 0.16;
    this.fit();
  }

  /* Frame whatever came out of the layout, so node count and canvas size stop
     mattering. */
  fit() {
    if (!this.w || !this.nodes.length) { this.needFit = true; return; }
    this.needFit = false;
    this.panX = 0; this.panY = 0;
    let r = 1;
    for (const n of this.nodes) {
      const d = this.dim === 3 ? Math.hypot(n.x, n.y, n.z) : Math.hypot(n.x, n.y);
      if (d > r) r = d;
    }
    this.zoom = Math.max(0.35, Math.min(2.6, (Math.min(this.w, this.h) / 2 - 54) / (r + 14)));
  }

  setDim(d) {
    // 2D flattens z to nothing, and a perfectly flat cloud has no force that can
    // push it back off the plane, so give it a shove on the way into 3D.
    if (d === 3 && this.dim !== 3) for (const n of this.nodes) n.z = (Math.random() - 0.5) * 150;
    this.dim = d;
    this.alpha = Math.max(this.alpha, 0.8);
    for (let i = 0; i < 200; i++) this.tick();
    this.fit();
  }
  setHighlight(set) { this.highlight = set; }
  select(id) { this.selected = id; }

  reset() {
    this.rx = -0.35; this.ry = 0.6; this.alpha = 0.9;
    for (let i = 0; i < 160; i++) this.tick();
    this.fit();
  }

  // ── simulation ────────────────────────────────────────────────────
  tick() {
    const n = this.nodes, N = n.length;
    if (!N) return;
    const a = this.alpha;

    for (let i = 0; i < N; i++) {
      const p = n[i];
      for (let j = i + 1; j < N; j++) {
        const q = n[j];
        let dx = p.x - q.x, dy = p.y - q.y, dz = this.dim === 3 ? p.z - q.z : 0;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 > 130000) continue;  // no long-range push: strays come home on the centring force alone
        if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
        const f = (3200 * a) / d2;
        const d = Math.sqrt(d2);
        const ux = dx / d, uy = dy / d, uz = dz / d;
        p.vx += ux * f; p.vy += uy * f; p.vz += uz * f;
        q.vx -= ux * f; q.vy -= uy * f; q.vz -= uz * f;
      }
    }

    for (const l of this.links) {
      const rest = l.kind === 'in' ? 62 : l.kind === 'kills' ? 46 : 88;
      let dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, dz = this.dim === 3 ? l.t.z - l.s.z : 0;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const f = ((d - rest) / d) * 0.075 * a;
      dx *= f; dy *= f; dz *= f;
      l.s.vx += dx; l.s.vy += dy; l.s.vz += dz;
      l.t.vx -= dx; l.t.vy -= dy; l.t.vz -= dz;
    }

    for (const p of n) {
      const pull = (p.deg ? 0.016 : 0.05) * a;   // loose nodes are held in tighter
      p.vx -= p.x * pull;
      p.vy -= p.y * pull;
      p.vz -= p.z * pull;
      if (this.dim === 2) { p.z *= 0.86; p.vz = 0; }
      if (p.fixed) { p.vx = p.vy = p.vz = 0; continue; }
      p.vx *= 0.82; p.vy *= 0.82; p.vz *= 0.82;
      p.x += p.vx; p.y += p.vy; p.z += p.vz;
    }

    this.alpha = Math.max(0.05, this.alpha * 0.994);
  }

  // ── projection ────────────────────────────────────────────────────
  project(p) {
    let x = p.x, y = p.y, z = this.dim === 3 ? p.z : 0;
    if (this.dim === 3) {
      const cy = Math.cos(this.ry), sy = Math.sin(this.ry);
      let x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const cx = Math.cos(this.rx), sx = Math.sin(this.rx);
      const y1 = y * cx - z1 * sx;
      z1 = y * sx + z1 * cx;
      x = x1; y = y1; z = z1;
    }
    const s = (FOV / (FOV + z)) * this.zoom;
    return { x: this.cx + x * s + this.panX, y: this.cy + y * s + this.panY, s, z };
  }

  // ── drawing ───────────────────────────────────────────────────────
  draw() {
    const c = this.ctx, W = this.w, H = this.h;
    c.clearRect(0, 0, W, H);
    this.cx = W / 2; this.cy = H / 2;

    const on = id => !this.highlight || this.highlight.has(id);
    const sel = this.selected;
    const near = sel ? this.neighbours.get(sel) : null;
    const lit = id => !sel || id === sel || (near && near.has(id));

    this._boxes = [];
    const pr = new Map();
    for (const n of this.nodes) pr.set(n.id, this.project(n));

    const ls = this.links.slice().sort((a, b) =>
      (pr.get(b.s.id).z + pr.get(b.t.id).z) - (pr.get(a.s.id).z + pr.get(a.t.id).z));

    for (const l of ls) {
      const a = pr.get(l.s.id), b = pr.get(l.t.id);
      const strong = lit(l.s.id) && lit(l.t.id) && on(l.s.id) && on(l.t.id);
      c.save();
      c.globalAlpha = strong ? (l.kind === 'kills' ? 0.8 : 0.5) : 0.07;
      c.strokeStyle = l.kind === 'kills' ? '#f87171' : '#9a9aa6';
      c.lineWidth = Math.max(0.5, (l.kind === 'kills' ? 1.4 : 1) * ((a.s + b.s) / 2));
      if (l.kind === 'kills') c.setLineDash([4, 4]);
      c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
      c.restore();
    }

    const ns = this.nodes.slice().sort((a, b) => pr.get(b.id).z - pr.get(a.id).z);
    for (const n of ns) {
      const p = pr.get(n.id);
      const r = Math.max(2, n.r * p.s);
      const strong = lit(n.id) && on(n.id);
      const isSel = n.id === sel, isHov = n.id === this.hover;

      c.save();
      c.globalAlpha = strong ? (n.dead ? 0.45 : 1) : 0.12;

      if (isSel || isHov) {
        c.beginPath(); c.arc(p.x, p.y, r + 6, 0, 7);
        c.fillStyle = n.colour + '22'; c.fill();
      }

      c.beginPath(); c.arc(p.x, p.y, r, 0, 7);
      if (n.dead) {
        c.strokeStyle = n.colour; c.lineWidth = 1.4; c.setLineDash([3, 3]); c.stroke();
      } else if (n.level === 'note') {
        c.strokeStyle = n.colour; c.lineWidth = 1.4; c.stroke();
      } else {
        c.fillStyle = n.colour; c.fill();
        c.globalAlpha = strong ? 0.35 : 0.08;
        c.strokeStyle = '#000'; c.lineWidth = 1; c.stroke();
      }

      if (n.ring && !isSel) {
        c.globalAlpha = strong ? 0.9 : 0.12; c.setLineDash([]);
        c.beginPath(); c.arc(p.x, p.y, r + 3.5, 0, 7);
        c.strokeStyle = '#a78bfa'; c.lineWidth = 1.3; c.stroke();
      }

      if (isSel) {
        c.globalAlpha = 1; c.setLineDash([]);
        c.beginPath(); c.arc(p.x, p.y, r + 4, 0, 7);
        c.strokeStyle = '#fff'; c.lineWidth = 1.4; c.stroke();
      }
      c.restore();

      const label = isSel || isHov || (strong && ['decision', 'artifact', 'section'].includes(n.level));
      if (label && p.s > 0.35 && (isSel || isHov || this._room(p.x, p.y + r + 8, n.label))) {
        c.save();
        c.globalAlpha = strong ? (isSel || isHov ? 1 : 0.62) : 0.1;
        c.font = (isSel || isHov ? '600 ' : '') + Math.round(11 * Math.min(1.15, p.s)) + 'px ui-sans-serif, system-ui, sans-serif';
        c.textAlign = 'center'; c.textBaseline = 'top';
        const txt = n.label.length > 34 ? n.label.slice(0, 33) + '…' : n.label;
        const w = c.measureText(txt).width;
        if (isSel || isHov) {
          c.fillStyle = 'rgba(10,10,12,.86)';
          c.fillRect(p.x - w / 2 - 6, p.y + r + 5, w + 12, 18);
        }
        c.fillStyle = n.dead ? '#8a8a92' : '#e7e7ea';
        c.fillText(txt, p.x, p.y + r + 8);
        if (n.dead) {
          c.strokeStyle = '#8a8a92'; c.lineWidth = 1; c.globalAlpha *= 0.9;
          c.beginPath(); c.moveTo(p.x - w / 2, p.y + r + 14); c.lineTo(p.x + w / 2, p.y + r + 14); c.stroke();
        }
        c.restore();
      }
    }
    this._pr = pr;
    this._boxes = [];
  }

  /* Cheap label collision test: a crowded graph with unreadable stacked text is
     worse than one where some labels wait for a hover. */
  _room(x, y, label) {
    const w = Math.min(210, label.length * 5.6), h = 15;
    const box = [x - w / 2, y, x + w / 2, y + h];
    for (const b of this._boxes) {
      if (box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]) return false;
    }
    this._boxes.push(box);
    return true;
  }

  frame() {
    if (!this.running) return;
    if (this.w > 0) {
      if (this.needFit) this.fit();
      if (this.dim === 3 && this.spin && !this.dragging) this.ry += 0.0016;
      this.tick();
      this.draw();
    }
    requestAnimationFrame(() => this.frame());
  }

  start() { if (!this.running) { this.running = true; this.resize(); this.frame(); } }
  stop() { this.running = false; }

  resize() {
    const r = this.cv.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = r.width; this.h = r.height;
    this.cv.width = Math.round(r.width * dpr);
    this.cv.height = Math.round(r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cx = this.w / 2; this.cy = this.h / 2;
  }

  // ── interaction ───────────────────────────────────────────────────
  at(mx, my) {
    if (!this._pr) return null;
    let best = null, bd = 1e9;
    for (const n of this.nodes) {
      const p = this._pr.get(n.id);
      const d = Math.hypot(p.x - mx, p.y - my);
      const r = Math.max(6, n.r * p.s) + 7;
      if (d < r && d < bd) { bd = d; best = n; }
    }
    return best;
  }

  _bind() {
    const cv = this.cv;
    let sx = 0, sy = 0, moved = 0, drag = null;

    const pos = ev => {
      const r = cv.getBoundingClientRect();
      return [ev.clientX - r.left, ev.clientY - r.top];
    };

    cv.addEventListener('pointerdown', ev => {
      const [x, y] = pos(ev);
      cv.setPointerCapture(ev.pointerId);
      this.dragging = true; moved = 0; sx = x; sy = y;
      const hit = this.at(x, y);
      if (hit && this.dim === 2) { drag = hit; hit.fixed = true; }
    });

    cv.addEventListener('pointermove', ev => {
      const [x, y] = pos(ev);
      if (!this.dragging) {
        const hit = this.at(x, y);
        const id = hit ? hit.id : null;
        if (id !== this.hover) { this.hover = id; cv.style.cursor = id ? 'pointer' : 'grab'; }
        return;
      }
      const dx = x - sx, dy = y - sy;
      moved += Math.abs(dx) + Math.abs(dy);
      sx = x; sy = y;
      if (drag) {
        drag.x += dx / this.zoom; drag.y += dy / this.zoom;
        this.alpha = Math.max(this.alpha, 0.35);
      } else if (this.dim === 3) {
        this.spin = false;
        this.ry += dx * 0.006;
        this.rx = Math.max(-1.4, Math.min(1.4, this.rx + dy * 0.006));
      } else {
        this.panX += dx; this.panY += dy;
      }
    });

    const up = ev => {
      if (!this.dragging) return;
      this.dragging = false;
      if (drag) { drag.fixed = false; drag = null; }
      if (moved < 4) {
        const [x, y] = pos(ev);
        const hit = this.at(x, y);
        this.selected = hit ? hit.id : null;
        this.onSelect(hit ? hit.ref : null, hit);
      }
    };
    cv.addEventListener('pointerup', up);
    cv.addEventListener('pointercancel', () => { this.dragging = false; if (drag) { drag.fixed = false; drag = null; } });

    cv.addEventListener('wheel', ev => {
      ev.preventDefault();
      const k = Math.exp(-ev.deltaY * 0.0016);
      this.zoom = Math.max(0.25, Math.min(4, this.zoom * k));
    }, { passive: false });

    new ResizeObserver(() => this.resize()).observe(cv);
  }
}
