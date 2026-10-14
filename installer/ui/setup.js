// Archer Setup: the stage's dot-matrix field and the step-by-step flow. DOM-only (no innerHTML),
// like archer.js. The backend is ArcherSetup.exe; every /api call carries the per-run token.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const TOKEN = $("meta[name=setup-token]").content;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, body) {
  const init = { method: body === undefined ? "GET" : "POST", headers: { "X-Setup-Token": TOKEN } };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const resp = await fetch("/api/" + path, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  return data;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : String(kid));
  return el;
}

function icon(id) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg"), use = document.createElementNS(NS, "use");
  use.setAttribute("href", "#" + id);
  svg.append(use);
  return svg;
}

let toastTimer;
function toast(text) {
  const t = $("[data-toast]");
  t.textContent = text;
  t.classList.add("is-shown");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("is-shown"), 1800);
}

// ------------------------------------------------------------------ the pixel mark (header)
const MARK = ["---#---", "--###--", "--###--", "-##-##-", "-##-##-", "##---##", "+:---:+"];
const INK = { "#": 1, "+": 0.62, ":": 0.34 };
function pixelMark(host) {
  const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 70 70");
  MARK.forEach((row, y) => [...row].forEach((ch, x) => {
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", x * 10 + 1.25); r.setAttribute("y", y * 10 + 1.25);
    r.setAttribute("width", 7.5); r.setAttribute("height", 7.5); r.setAttribute("rx", 1.9);
    r.setAttribute("class", INK[ch] ? "px-on" : "px-off");
    if (INK[ch] && INK[ch] < 1) r.setAttribute("fill-opacity", INK[ch]);
    r.style.setProperty("--i", (MARK.length - 1 - y) * 2 + x);
    svg.append(r);
  }));
  host.append(svg);
}

// ------------------------------------------------------------------ the field
// A grid of rounded dots. Each step gives it a target shape (sampled from an offscreen drawing);
// dots ease toward their new brightness in a wave from the centre. On top: pointer glow, twinkle,
// a scanning band, progress (the shape lights from the bottom up) and ripples.
const hash = (x, y) => { let h = (x * 374761393 + y * 668265263) ^ 0x5bd1e995; h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967295; };

class Field {
  constructor(canvas) {
    this.c = canvas; this.g = canvas.getContext("2d");
    this.pitch = 9; this.kind = "none"; this.progress = null; this.scan = false; this.glow = 0; this.ripples = [];
    this.mx = this.my = -1e4;
    const stage = document.querySelector(".shell");
    stage.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); this.mx = e.clientX - r.left; this.my = e.clientY - r.top; });
    stage.addEventListener("pointerleave", () => { this.mx = this.my = -1e4; });
    stage.addEventListener("pointerdown", (e) => { const r = canvas.getBoundingClientRect(); this.ripple(e.clientX - r.left, e.clientY - r.top, 0.5); });
    new ResizeObserver(() => this.layout()).observe(canvas);
    requestAnimationFrame((t) => this.frame(t));
  }

  layout() {
    const w = this.c.clientWidth, h = this.c.clientHeight, dpr = Math.min(devicePixelRatio || 1, 1.5);
    if (!w || !h) return;
    this.c.width = Math.round(w * dpr); this.c.height = Math.round(h * dpr);
    this.g.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h; this.dpr = dpr;
    const p = this.pitch;
    this.cols = Math.floor(w / p); this.rows = Math.floor(h / p);
    this.ox = (w - this.cols * p) / 2 + p / 2; this.oy = (h - this.rows * p) / 2 + p / 2;
    const n = this.cols * this.rows;
    this.cur = new Float32Array(n); this.from = new Float32Array(n); this.to = new Float32Array(n);
    this.t0 = new Float32Array(n); this.seed = new Float32Array(n);
    for (let k = 0; k < n; k++) this.seed[k] = hash(k % this.cols, (k / this.cols) | 0);
    // One pre-rendered dot, stamped with varying alpha: far cheaper than a path per dot.
    const sq = p * 0.74, sp = document.createElement("canvas");
    sp.width = sp.height = Math.ceil(sq * dpr) + 2;
    const sg = sp.getContext("2d"); sg.scale(dpr, dpr); sg.fillStyle = "#ededed";
    sg.beginPath(); sg.roundRect(1 / dpr, 1 / dpr, sq, sq, sq * 0.22); sg.fill();
    this.sprite = sp; this.sq = sq;
    this.background = document.createElement("canvas");
    this.background.width = this.c.width; this.background.height = this.c.height;
    const bg = this.background.getContext("2d"); bg.scale(dpr, dpr); bg.globalAlpha = .05;
    for (let r = 0; r < this.rows; r++) for (let c = 0; c < this.cols; c++)
      bg.drawImage(sp, this.ox + c * p - sq / 2, this.oy + r * p - sq / 2, sp.width / dpr, sp.height / dpr);
    this.shape(this.kind, true);
  }

  // Keep the morphing pixel artwork in the free margin beside the content.
  sample(kind) {
    const SS = 4, cols = this.cols, rows = this.rows;
    const map = new Float32Array(cols * rows);
    const margin = Math.max(0, (this.w - 760) / 2);
    this.box = null; this.caret = null;
    if (margin < 90) return map;
    const off = document.createElement("canvas");
    off.width = cols * SS; off.height = rows * SS;
    const g = off.getContext("2d"), scale = SS / this.pitch;
    g.scale(scale, scale);
    const size = Math.min(margin - 36, 190);
    const x = this.w - margin / 2, y = this.h * .48;
    g.translate(x - size / 2, y - size / 2); g.scale(size / 100, size / 100);
    g.fillStyle = g.strokeStyle = "#fff"; g.lineWidth = 5; g.lineCap = "round"; g.lineJoin = "round";
    if (kind === "ascent") {
      g.beginPath(); g.moveTo(50, 3); g.lineTo(95, 95); g.lineTo(71, 95); g.lineTo(50, 49); g.lineTo(29, 95); g.lineTo(5, 95); g.closePath(); g.fill();
    } else if (kind === "target") {
      for (const radius of [43, 27, 10]) { g.beginPath(); g.arc(50, 50, radius, 0, Math.PI * 2); g.stroke(); }
    } else if (kind === "window") {
      g.strokeRect(5, 12, 90, 76); g.beginPath(); g.moveTo(5, 31); g.lineTo(95, 31); g.stroke();
      for (let i = 0; i < 3; i++) g.fillRect(13 + i * 9, 20, 3, 3);
      g.fillRect(16, 43, 20, 4); g.fillRect(16, 55, 20, 4); g.fillRect(45, 43, 36, 5); g.fillRect(45, 56, 30, 4); g.fillRect(65, 72, 17, 6);
    } else if (kind === "caret") {
      g.strokeRect(3, 29, 94, 42); g.beginPath(); g.arc(23, 48, 8, 0, Math.PI * 2); g.moveTo(29, 54); g.lineTo(35, 60); g.moveTo(51, 39); g.lineTo(51, 61); g.stroke();
      this.caret = [(x + size * .01) / this.pitch, (x + size * .05) / this.pitch];
    }
    const data = g.getImageData(0, 0, off.width, off.height).data;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      let a = 0;
      for (let j = 0; j < SS; j++) for (let i = 0; i < SS; i++) a += data[((r * SS + j) * off.width + c * SS + i) * 4 + 3];
      map[r * cols + c] = a / (SS * SS * 255);
    }
    this.box = { top: (y - size / 2) / this.pitch, bottom: (y + size / 2) / this.pitch };
    return map;
  }

  shape(kind, instant) {
    this.kind = kind; this.dirty = true;
    if (!this.cols) return;
    const map = this.sample(kind), now = performance.now(), cx = this.cols / 2, cy = this.rows * 0.4;
    const maxD = Math.hypot(cx, this.rows);
    this.settlesAt = now + 2400; this.settled = false;
    this.active = [];
    for (let k = 0; k < map.length; k++) {
      if (map[k] > .001 || this.cur[k] > .001) this.active.push(k);
      const c = k % this.cols, r = (k / this.cols) | 0;
      this.from[k] = instant ? map[k] : this.cur[k];
      this.to[k] = map[k];
      // A wave outward from the shape's centre, roughened per dot so it never looks mechanical.
      this.t0[k] = now + (instant || REDUCED ? -1e5 : (Math.hypot(c - cx, r - cy) / maxD) * 520 + this.seed[k] * 260);
    }
  }

  // Scatter every dot first, then let the shape assemble out of the noise (the opening moment).
  intro(kind) {
    this.kind = kind;
    if (!this.cols || REDUCED) { this.shape(kind, true); return; }
    for (let k = 0; k < this.cur.length; k++) this.cur[k] = this.seed[k] > 0.82 ? this.seed[k] * 0.9 : 0;
    this.shape(kind);
    const now = performance.now();
    for (let k = 0; k < this.t0.length; k++) this.t0[k] = now + 250 + this.seed[k] * 1100;
  }

  ripple(x, y, strength = 1) {
    if (REDUCED) return;
    this.ripples = this.ripples.slice(-3);
    this.ripples.push({ x: x ?? this.w / 2, y: y ?? this.h * 0.4, t: performance.now(), s: strength });
  }

  frame(now) {
    requestAnimationFrame((t) => this.frame(t));
    if (!this.cols || document.hidden || now - (this.lastFrame || 0) < 1000 / 30) return;
    if (REDUCED && !this.dirty && this.lastProgress === this.progress) return;
    this.lastFrame = now; this.lastProgress = this.progress; this.dirty = false;
    if (!this.settled && now > this.settlesAt) { this.active = this.active.filter(k => this.to[k] > .001); this.settled = true; }
    const g = this.g, p = this.pitch, cols = this.cols, rows = this.rows, sp = this.sprite;
    const DUR = 700, half = this.sq / 2, base = 0.05;
    g.clearRect(0, 0, this.w, this.h);
    g.drawImage(this.background, 0, 0, this.w, this.h);
    this.ripples = this.ripples.filter((r) => now - r.t < 2400);
    const box = this.box, prog = this.progress;
    const progRow = box && prog != null ? box.bottom - (box.bottom - box.top) * prog : null;
    const scanY = this.scan ? ((now / 2300) % 1) * (rows + 16) - 8 : null;
    const blink = this.caret && Math.floor(now / 560) % 2 === 0;
    const drift = REDUCED ? 0 : Math.sin(now / 6500) * 20;
    const effects = !REDUCED && (this.scan || this.ripples.length || this.mx > -1e3);
    const count = effects ? cols * rows : this.active.length;
    for (let n = 0; n < count; n++) {
        const k = effects ? n : this.active[n], r = (k / cols) | 0, c = k % cols;
        const y = this.oy + r * p, x = this.ox + c * p;
        let t = (now - this.t0[k]) / DUR;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        const e = 1 - Math.pow(1 - t, 3);
        const v = (this.cur[k] = this.from[k] + (this.to[k] - this.from[k]) * e);
        let a = v * 0.82;
        if (v > 0.02) {
          if (progRow != null) {
            // Install progress: the shape fills from the bottom; a bright seam rides the edge.
            const lit = r >= progRow ? 1 : 0.22, seam = Math.exp(-Math.pow((r - progRow) / 1.3, 2));
            a = v * 0.82 * lit + seam * v * 0.6;
          }
          if (this.caret && !blink && c >= this.caret[0] - 0.5 && c <= this.caret[1] + 0.5) a = v * 0.15;
          if (!REDUCED) a += Math.sin(now * 0.0021 + this.seed[k] * 40) * 0.05 * v;  // twinkle
          a += this.glow * v * 0.25;
        }
        if (scanY != null) a += Math.exp(-Math.pow((r - scanY) / 2.2, 2)) * (0.1 + v * 0.5);
        if (this.mx > -1e3) {
          const dx = x - this.mx, dy = y - this.my, d2 = dx * dx + dy * dy;
          if (d2 < 12000) a += Math.exp(-d2 / 2600) * (0.22 + v * 0.35);
        }
        for (const rp of this.ripples) {
          const age = (now - rp.t) / 1000, rad = age * 520, d = Math.hypot(x - rp.x, y - rp.y);
          a += Math.exp(-Math.pow((d - rad) / 26, 2)) * 0.7 * rp.s * (1 - age / 2.4);
        }
        if (a < 0.012) continue;
        g.globalAlpha = a > 1 ? 1 : a;
        g.drawImage(sp, x - half, y - half + drift, sp.width / this.dpr, sp.height / this.dpr);
    }
    g.globalAlpha = 1;
  }
}

// ------------------------------------------------------------------ flow
const ORDER = ["welcome", "check", "search", "install", "configure", "browser", "done"];
const S = { view: null, info: null, check: null, sxMode: "", sxUrl: "", launched: false, timers: [] };
let field;

const nav = {
  back: $("[data-back]"), primary: $("[data-primary]"), label: $("[data-primary-label]"),
  secondary: $("[data-secondary]"), status: $("[data-status]"),
};
let onPrimary = null, onSecondary = null, onBack = null;
nav.primary.addEventListener("click", () => onPrimary && !nav.primary.disabled && onPrimary());
nav.secondary.addEventListener("click", () => onSecondary && onSecondary());
nav.back.addEventListener("click", () => onBack && onBack());

function setNav({ label, primary, disabled = false, secondary = null, back = null, status = "", tone = "" }) {
  nav.label.textContent = label;
  onPrimary = primary;
  nav.primary.disabled = disabled;
  nav.primary.classList.toggle("is-hidden", !primary);
  onSecondary = secondary && secondary.run;
  nav.secondary.hidden = !secondary;
  if (secondary) nav.secondary.textContent = secondary.label;
  onBack = back;
  nav.back.classList.toggle("is-hidden", !back);
  status_(status, tone);
}
function status_(text, tone = "") {
  nav.status.textContent = text;
  nav.status.className = "status" + (tone ? " is-" + tone : "");
}
function later(fn, ms) { const id = setTimeout(fn, ms); S.timers.push(id); return id; }
function clearTimers() { S.timers.forEach(clearTimeout); S.timers = []; }
function readout(text) { $(".setup-progress").setAttribute("aria-description", text || "Installation progress"); }
function busy(on) { document.body.classList.toggle("is-busy", on); }

async function go(view) {
  clearTimers();
  const prev = S.view && $(`[data-view=${S.view}]`);
  if (prev && !REDUCED) { prev.classList.add("is-leaving"); await sleep(170); }
  if (prev) { prev.hidden = true; prev.classList.remove("is-leaving", "is-entering"); }
  S.view = view;
  const el = $(`[data-view=${view}]`);
  $$("[data-rise]", el).forEach((n, i) => n.style.setProperty("--d", i));
  el.hidden = false;
  el.scrollTop = 0;
  el.classList.remove("is-entering"); void el.offsetWidth; el.classList.add("is-entering");
  const at = ORDER.indexOf(view);
  $$("[data-rail-step]").forEach((li) => {
    const i = ORDER.indexOf(li.dataset.railStep);
    li.classList.toggle("is-current", i === at);
    if (i === at) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    li.classList.toggle("is-done", i < at && !li.classList.contains("is-skipped"));
  });
  field.progress = null; field.scan = false; field.glow = 0; readout("");
  VIEWS[view]();
}

function setState(li, state, detail) {
  li.classList.remove("is-busy", "is-ok", "is-warn", "is-bad", "is-skip");
  if (state) li.classList.add("is-" + state);
  const glyph = $(".state", li);
  glyph.replaceChildren();
  const id = { ok: "i-check", warn: "i-dash", bad: "i-x", skip: "i-dash" }[state];
  if (id) glyph.append(icon(id));
  if (detail !== undefined) { const d = $(".detail, em", li); if (d) d.textContent = detail; }
}

// Pixel progress: a row of cells, lit up to the current fraction with a glowing leading edge.
function dotbar(name) {
  const bar = $(`[data-dotbar=${name}]`);
  if (!bar.children.length) {
    const n = 40;
    bar.style.setProperty("--cells", n);
    for (let i = 0; i < n; i++) { const c = h("i"); c.style.setProperty("--k", i); bar.append(c); }
  }
  return bar;
}
const shown = {};
function meter(name, fraction, done) {
  const bar = dotbar(name), cells = [...bar.children], lit = Math.round(fraction * cells.length);
  cells.forEach((c, i) => { c.classList.toggle("on", i < lit); c.classList.toggle("edge", !done && i === lit - 1); });
  bar.classList.toggle("is-done", !!done);
  // The counter eases toward the real value rather than jumping.
  const pct = $(`[data-pct=${name}]`), target = Math.round(fraction * 100);
  cancelAnimationFrame(shown[name + "raf"]);
  const step = () => {
    const cur = shown[name] || 0;
    if (cur === target) return;
    shown[name] = cur + Math.sign(target - cur) * Math.max(1, Math.round(Math.abs(target - cur) / 6));
    pct.textContent = shown[name];
    shown[name + "raf"] = requestAnimationFrame(step);
  };
  step();
}
function consoleLines(name, lines) {
  const pre = $(`[data-console=${name}]`);
  for (const line of lines) {
    pre.querySelectorAll(".last").forEach((n) => n.classList.remove("last"));
    pre.append(h("span", { class: "last" }, line + "\n"));
  }
  while (pre.childNodes.length > 300) pre.firstChild.remove();
  if (lines.length) pre.scrollTop = pre.scrollHeight;
}

// Polls a backend job until it ends, reporting each snapshot.
async function follow(name, onTick) {
  let since = 0;
  for (;;) {
    let snap;
    try { snap = await api(`job?name=${name}&since=${since}`); }
    catch { await sleep(800); continue; }
    since = snap.next ?? since;
    onTick(snap);
    if (snap.state !== "running") return snap;
    await sleep(350);
  }
}

// ------------------------------------------------------------------ views
const VIEWS = {
  welcome() {
    field.shape("ascent");
    readout("");
    const dir = $("[data-dir]");
    dir.textContent = S.info.dir;
    dir.title = S.info.dir;
    $("[data-welcome-title]").textContent = S.info.existing ? "Update Archer" : "Install Archer";
    $("[data-existing]").hidden = !S.info.existing;
    setNav({
      label: "Next",
      primary: () => go("check"),
      status: S.info.dry ? "Preview mode: nothing will be installed." : "For you only. No admin rights needed.",
    });
  },

  async check() {
    field.shape("ascent"); field.scan = true; readout("Scanning");
    busy(true);
    const rows = Object.fromEntries($$("[data-check]").map((li) => [li.dataset.check, li]));
    Object.values(rows).forEach((li) => setState(li, "busy", ""));
    $("[data-check-note]").hidden = true;
    setNav({ label: "Continue", primary: null, back: () => go("welcome"), status: "Checking…" });
    const started = performance.now();
    let c;
    try { c = await api("check", {}); }
    catch (e) { busy(false); status_(e.message, "bad"); return; }
    await sleep(Math.max(0, 700 - (performance.now() - started)));
    S.check = c;
    const d = c.docker;
    const results = [
      ["windows", "ok", `${c.windows} · ${c.arch}`],
      ["disk", c.freeGb < 0 ? "warn" : c.freeGb < 1 ? "bad" : "ok", c.freeGb < 0 ? "unknown" : `${c.freeGb} GB free`],
      ["online", c.online ? "ok" : "bad", c.online ? "connected" : "offline"],
      ["docker", d.installed ? "ok" : "skip", !d.installed ? "not installed" : d.running ? "running" : "installed, stopped"],
    ];
    for (const [key, state, detail] of results) {
      if (S.view !== "check") return;
      setState(rows[key], state, detail);
      field.ripple(undefined, undefined, 0.25);
      await sleep(REDUCED ? 0 : 260);
    }
    busy(false); field.scan = false; readout("");
    if (!c.online || c.freeGb >= 0 && c.freeGb < 1) {
      setNav({ label: "Check again", primary: () => go("check"), back: () => go("welcome"),
        status: !c.online ? "Archer needs the internet to download Python and its packages." : "Free up at least 1 GB and check again.", tone: "bad" });
      return;
    }
    $('[data-rail-step=search]').classList.toggle("is-skipped", !d.installed);
    if (!d.installed) {
      const note = $("[data-check-note]");
      note.textContent = "Docker isn't installed, so Archer will get results from DuckDuckGo. Install Docker Desktop any time to switch to your own SearXNG in Archer's settings.";
      note.hidden = false;
      setNav({ label: "Install Archer", back: () => go("welcome"), status: "Ready to install.", tone: "ok",
        primary: async () => { await api("searxng/choice", { mode: "duckduckgo" }); S.sxMode = "duckduckgo"; go("install"); } });
      return;
    }
    setNav({ label: "Continue", primary: () => go("search"), back: () => go("welcome"), status: "All good.", tone: "ok" });
  },

  search() {
    field.shape("target"); readout("SearXNG");
    $$("[data-sx]").forEach((n) => (n.hidden = true));
    $("[data-sx-error]").hidden = true;
    const skip = { label: "Install with DuckDuckGo", run: async () => {
      await api("searxng/choice", { mode: "duckduckgo" }); S.sxMode = "duckduckgo";
      $('[data-rail-step=search]').classList.add("is-skipped"); go("install");
    } };
    const back = () => go("welcome");
    const d = S.check.docker, sx = d.searxng || { state: "missing" };

    if (sx.state === "ready") {
      $$("[data-sx-url]").forEach((n) => (n.textContent = sx.url));
      $("[data-sx-container]").textContent = sx.container ? ` in container ${sx.container}` : "";
      $("[data-sx=ready]").hidden = false;
      field.glow = 0.4; field.ripple();
      api("searxng/choice", { mode: "searxng", url: sx.url, container: sx.container || "" });
      S.sxMode = "searxng"; S.sxUrl = sx.url;
      setNav({ label: "Install Archer", primary: () => go("install"), back, status: "Your SearXNG is ready.", tone: "ok" });
      return;
    }

    if (!d.running) {
      const card = $("[data-sx=docker-off]"), btn = $("[data-start-docker]");
      card.hidden = false;
      btn.hidden = !d.canStart;
      $("[data-docker-hint]").textContent = d.canStart
        ? "Start it and Archer carries on as soon as Docker is ready."
        : "Open Docker Desktop, wait until it says it's running, then check again.";
      const wait = async () => {
        btn.disabled = true; busy(true);
        status_("Waiting for Docker to start… this can take a minute.");
        for (let i = 0; i < 80 && S.view === "search"; i++) {
          await sleep(3000);
          const c = await api("check", {}).catch(() => null);
          if (c && c.docker.running) { S.check = c; busy(false); btn.disabled = false; VIEWS.search(); return; }
        }
        busy(false); btn.disabled = false;
        status_("Docker still isn't answering. Try again, or skip for now.", "bad");
      };
      btn.onclick = async () => { await api("docker/start", {}); wait(); };
      setNav({ label: "Check again", back, secondary: skip, primary: async () => {
        const c = await api("check", {}); S.check = c; VIEWS.search();
        if (!c.docker.running) status_("Docker isn't running yet.", "bad");
      } });
      return;
    }

    if (sx.state === "nojson") {
      $$("[data-sx-url]").forEach((n) => (n.textContent = sx.url));
      $("[data-sx=nojson]").hidden = false;
    }
    const offer = $("[data-sx=offer]");
    offer.hidden = false;
    const stopped = sx.state === "stopped";
    $("[data-offer-title]").textContent = stopped ? "Start Archer's SearXNG" : "Set up SearXNG for Archer";
    $("[data-offer-text]").textContent = stopped
      ? "The archer-searxng container from an earlier install is stopped. Start it and Archer will use it."
      : "Recommended. Docker downloads the official image (about 100 MB) and runs it on this PC only: more engines, no rate limits.";
    setNav({ label: stopped ? "Start SearXNG" : "Set up SearXNG", primary: () => runSearxng(skip), back, secondary: skip });
  },

  async install() {
    field.shape("ascent"); field.progress = 0; readout("Installing · 0%");
    $("[data-install-error]").hidden = true;
    $("[data-install-title]").textContent = S.info.existing ? "Updating Archer" : "Installing Archer";
    setNav({ label: "Installing…", primary: () => {}, disabled: true, status: "This usually takes one to three minutes." });
    busy(true);
    const tasks = Object.fromEntries($$("[data-tasks=install] li").map((li) => [li.dataset.task, li]));
    const order = Object.keys(tasks), began = {};
    Object.values(tasks).forEach((li) => setState(li, "", ""));
    try { await api("install", { autostart: $("[data-autostart]").checked }); }
    catch (e) { return failInstall(e.message); }
    const snap = await follow("install", (s) => {
      const at = order.indexOf(s.step);
      order.forEach((key, i) => {
        if (i < at || s.state === "done") {
          if (!tasks[key].classList.contains("is-ok")) setState(tasks[key], "ok", began[key] ? `${((performance.now() - began[key]) / 1000).toFixed(1)}s` : "");
        } else if (i === at) {
          began[key] = began[key] || performance.now();
          if (!tasks[key].classList.contains("is-busy")) setState(tasks[key], s.state === "failed" ? "bad" : "busy", "");
        }
      });
      meter("install", s.progress, s.state === "done");
      field.progress = s.progress;
      readout(s.state === "done" ? "Installed" : `Installing · ${Math.round(s.progress * 100)}%`);
      consoleLines("install", s.log || []);
      const last = (s.log || []).slice(-1)[0];
      if (last && s.state === "running") status_(last);
    });
    busy(false);
    if (snap.state !== "done") return failInstall(snap.error || "The install stopped.");
    field.progress = 1; field.glow = 0.5; field.ripple(); later(() => field.ripple(undefined, undefined, 0.6), 380);
    status_("Archer is installed.", "ok");
    setNav({ label: "Continue", primary: () => go("configure"), status: "Archer is installed.", tone: "ok" });
  },

  configure() {
    field.shape("window"); readout("Your settings");
    const waiting = $("[data-waiting]"), text = $("[data-waiting-text]");
    waiting.classList.remove("is-ok");
    text.textContent = "Open Archer setup when you are ready.";
    const open = async () => {
      const r = await api("launch", {}).catch(() => ({ ok: false }));
      if (!r.ok) status_("Couldn't start Archer. Use the Archer shortcut on your desktop.", "bad");
      else {
        S.launched = true; text.textContent = "Waiting for you to finish in the Archer window…";
        toast("Archer is opening");
        setNav({ label: "Continue", primary: () => go("browser"), secondary: { label: "Reopen Archer", run: open } });
      }
    };
    setNav({ label: "Open Archer setup", primary: open, secondary: { label: "Set up later", run: () => go("browser") } });
    const poll = async () => {
      if (S.view !== "configure") return;
      if (!S.launched) { later(poll, 3000); return; }
      const r = await api("onboarded").catch(() => ({}));
      if (r.onboarded) {
        waiting.classList.add("is-ok");
        text.textContent = "Archer setup is complete. Select Continue when you are ready.";
        field.glow = 0.4; field.ripple();
        status_("Archer is configured.", "ok");
        return;
      }
      later(poll, 1500);
    };
    later(poll, 1500);
  },

  browser() {
    field.shape("caret"); readout("Address bar");
    const b = S.info.browser;
    $("[data-browser-name]").textContent = b.id === "other" ? "Not detected" : b.name;
    $("[data-browser-letter]").textContent = b.id === "other" ? "?" : b.name.replace(/^(Google|Microsoft) /, "")[0];
    $("[data-steps-firefox]").hidden = !b.firefox;
    $("[data-steps-chromium]").hidden = b.firefox;
    const open = $("[data-open-settings]");
    open.hidden = !b.canOpen;
    $("[data-open-settings-label]").textContent = `Open ${b.name.replace(/^(Google|Microsoft) /, "")} settings`;
    open.onclick = async () => {
      const r = await api("browser/settings", {});
      toast(r.ok ? `Opened ${b.name}'s search settings` : "Couldn't open it. Open your browser's settings and search for “search engine”.");
    };
    const rows = [["Name", "Archer"], [b.firefox ? "Keyword" : "Shortcut", "ar"], ["URL with %s in place of query", S.info.searchUrl]];
    if (!b.firefox) rows.push(["Suggestions URL", S.info.suggestUrl]);
    $("[data-copies]").replaceChildren(...rows.map(([label, value]) => {
      const btn = h("button", { type: "button", class: "btn" }, icon("i-copy"), "Copy");
      btn.addEventListener("click", async () => {
        await copy(value);
        btn.replaceChildren(icon("i-check"), "Copied");
        btn.classList.add("is-done");
        setTimeout(() => { btn.replaceChildren(icon("i-copy"), "Copy"); btn.classList.remove("is-done"); }, 1600);
      });
      return h("div", { class: "copy-row" }, h("span", {}, label), h("code", { title: value }, value), btn);
    }));
    setNav({ label: "Finish", primary: () => go("done"), back: () => go("configure"),
      secondary: { label: "Open Archer", run: () => api("open", {}) } });
  },

  done() {
    field.shape("ascent"); field.glow = 0.55; readout("Ready");
    $$("[data-rail-step]").forEach((li) => { li.classList.remove("is-current"); if (!li.classList.contains("is-skipped")) li.classList.add("is-done"); });
    [0, 420, 900].forEach((ms, i) => later(() => field.ripple(undefined, undefined, 1 - i * 0.25), ms));
    const results = S.sxMode === "searxng" ? `Your SearXNG at ${S.sxUrl || "127.0.0.1"}` : S.sxMode === "duckduckgo" ? "DuckDuckGo" : "As chosen in Archer";
    $("[data-summary]").replaceChildren(
      h("li", {}, h("b", {}, "Address"), h("span", {}, S.info.baseUrl)),
      h("li", {}, h("b", {}, "Results"), h("span", {}, results)),
      h("li", {}, h("b", {}, "Startup"), h("span", {}, $("[data-autostart]").checked ? "With Windows, Docker and SearXNG included" : "From the Archer shortcut")),
      h("li", {}, h("b", {}, "Folder"), h("span", {}, S.info.dir)),
    );
    const form = $("[data-try]");
    form.onsubmit = (e) => {
      e.preventDefault();
      const q = form.q.value.trim();
      api("open", { q }); toast(q ? `Searching for “${q}”` : "Opening Archer");
      field.ripple();
    };
    later(() => form.q.focus(), 600);
    setNav({ label: "Close", primary: async () => { await api("quit", {}).catch(() => {}); window.close(); } });
  },
};

async function runSearxng(skip) {
  $$("[data-sx]").forEach((n) => (n.hidden = n.dataset.sx !== "work"));
  $("[data-sx-error]").hidden = true;
  setNav({ label: "Setting up…", primary: () => {}, disabled: true, status: "Docker is working. The first download takes a minute or two." });
  busy(true); field.scan = true;
  const tasks = Object.fromEntries($$("[data-tasks=sx] li").map((li) => [li.dataset.task, li]));
  const order = Object.keys(tasks);
  Object.values(tasks).forEach((li) => setState(li, ""));
  try { await api("searxng/setup", {}); }
  catch (e) { return failSx(e.message, skip); }
  const snap = await follow("searxng", (s) => {
    const at = order.indexOf(s.step);
    order.forEach((key, i) => {
      if (i < at || s.state === "done") { if (!tasks[key].classList.contains("is-ok")) setState(tasks[key], "ok"); }
      else if (i === at && !tasks[key].classList.contains("is-busy")) setState(tasks[key], "busy");
    });
    meter("sx", s.progress, s.state === "done");
    readout(`SearXNG · ${Math.round(s.progress * 100)}%`);
    consoleLines("sx", s.log || []);
  });
  busy(false); field.scan = false;
  if (snap.state !== "done") return failSx(snap.error, skip);
  S.sxMode = "searxng"; S.sxUrl = snap.result.url;
  field.glow = 0.45; field.ripple(); readout("SearXNG ready");
  setNav({ label: "Install Archer", primary: () => go("install"), status: `SearXNG is running at ${snap.result.url}.`, tone: "ok" });
}

function failSx(message, skip) {
  busy(false); field.scan = false;
  const err = $("[data-sx-error]");
  err.textContent = message || "SearXNG couldn't be set up.";
  err.hidden = false;
  setNav({ label: "Try again", primary: () => runSearxng(skip), back: () => go("welcome"), secondary: skip, status: "" });
}

function failInstall(message) {
  busy(false);
  const err = $("[data-install-error]");
  err.textContent = message;
  err.hidden = false;
  $(".log").open = true;
  field.progress = null; readout("Stopped");
  setNav({ label: "Try again", primary: () => go("install"), back: () => go("welcome"), status: "Nothing is half-done: trying again picks up where it stopped.", tone: "bad" });
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* fall through */ }
  const ta = h("textarea", { class: "offscreen" });
  ta.value = text; document.body.append(ta); ta.select();
  document.execCommand("copy"); ta.remove();
}

// ------------------------------------------------------------------ start
async function start() {
  pixelMark($("[data-pxmark]"));
  field = new Field($("[data-field]"));
  await document.fonts.load('600 40px "DM Sans"').catch(() => {});
  S.info = await api("state");
  field.layout();
  field.intro("ascent");
  setInterval(() => api("ping", {}).catch(() => {}), 5000);

  $("[data-pick-dir]").addEventListener("click", async () => {
    const r = await api("folder", {});
    S.info = await api("state");
    $("[data-dir]").textContent = r.dir;
    $("[data-dir]").title = r.dir;
    $("[data-existing]").hidden = !S.info.existing;
    nav.label.textContent = "Next";
    $("[data-welcome-title]").textContent = S.info.existing ? "Update Archer" : "Install Archer";
  });

  await sleep(REDUCED ? 0 : 500);
  document.body.classList.remove("is-intro");
  go("welcome");
}
start();
