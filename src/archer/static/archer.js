// Archer results page: lazy modules and small interactive widgets.
// Everything is built with DOM APIs + textContent; no HTML from the network is injected.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const isHttp = (u) => typeof u === "string" && /^https?:\/\//.test(u);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

function img(src, alt = "") {
  if (!isHttp(src)) return null;
  const i = el("img", { src, alt, loading: "lazy", referrerpolicy: "no-referrer" });
  i.addEventListener("error", () => i.remove());
  return i;
}

// <svg><use href="#u-NAME"/></svg> from the inline icon set.
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#u-${name}`);
  svg.setAttribute("class", "line-icon");
  svg.append(use);
  return svg;
}

// ------------------------------------------- favicons over letter badges
// Shown on a light chip once loaded (many favicons are dark-on-transparent); on a miss the letter stays.
function wireFavicon(imgEl) {
  const badge = imgEl.parentElement;
  const ok = () => { if (imgEl.naturalWidth > 1) badge.classList.add("has-icon"); else imgEl.remove(); };
  if (imgEl.complete) { imgEl.naturalWidth ? ok() : imgEl.remove(); }
  else { imgEl.addEventListener("load", ok); imgEl.addEventListener("error", () => imgEl.remove()); }
}
$$(".badge-icon").forEach(wireFavicon);

const cache = new Map();
function getJSON(url) {
  if (!cache.has(url)) cache.set(url, fetch(url).then((r) => (r.ok ? r.json() : [])).catch(() => []));
  return cache.get(url);
}
const strip = (kind, q) => getJSON(`/api/strip/${kind}?q=${encodeURIComponent(q)}`);

// ------------------------------------------------------------ search box
const box = $(".top .searchbar input");
$(".searchbar-clear")?.addEventListener("click", () => { box.value = ""; box.focus(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && box && document.activeElement !== box && !/input|textarea/i.test(document.activeElement.tagName)) {
    e.preventDefault(); box.focus(); box.select();
  }
});

// ---------------------------------------------------- strips (lazy)
function card(kind, item) {
  const media = el("div", { class: "card-img" + (item.thumb ? "" : " card-img--empty") });
  const picture = img(item.thumb);
  if (picture) media.append(picture); else media.textContent = kind === "news" ? "📰" : "▶";
  if (kind === "videos" && item.length) media.append(el("span", { class: "length", text: item.length }));
  if (kind === "images") return el("a", { class: "card", href: item.url, title: item.title }, media);
  return el("a", { class: "card", href: item.url },
    media,
    el("div", { class: "card-body" },
      el("span", { class: "card-host", text: item.host }),
      el("span", { class: "card-title", text: item.title }),
      item.ago ? el("span", { class: "card-age", text: item.ago }) : null));
}

for (const section of $$("[data-lazy]")) {
  const kind = section.dataset.lazy;
  strip(kind, section.dataset.q).then((items) => {
    items = items.filter((i) => isHttp(i.url) && (kind === "news" || i.thumb));
    if (kind === "news") items = [...items.filter((i) => i.thumb), ...items.filter((i) => !i.thumb)];
    if (items.length < 2) return section.remove();
    $(".strip-row", section).replaceChildren(...items.slice(0, kind === "images" ? 12 : 8).map((i) => card(kind, i)));
  });
}

// Load the first candidate image that actually decodes; null if none do.
function firstLoading(candidates) {
  return new Promise((resolve) => {
    const next = () => {
      const c = candidates.shift();
      if (!c) return resolve(null);
      const probe = new Image();
      probe.referrerPolicy = "no-referrer";
      probe.onload = () => resolve(c);
      probe.onerror = next;
      probe.src = c.thumb;
    };
    next();
  });
}

// ------------------------------------------------- entity hero: collage
for (const collage of $$("[data-collage-q]")) {
  strip("images", collage.dataset.collageQ).then(async (items) => {
    const pool = items.filter((i) => isHttp(i.thumb) && isHttp(i.url));
    const side = $(".collage-side", collage);
    const picked = [];
    for (const _ of $$(".collage-slot", collage)) {
      const pic = await firstLoading(pool);
      if (pic) picked.push(pic);
    }
    if (!picked.length) { collage.removeAttribute("data-collage-q"); return; }
    side.replaceChildren(...picked.map((p) =>
      el("a", { class: "collage-slot", href: p.url, title: p.title }, img(p.thumb, p.title))));
    side.style.gridTemplateRows = `repeat(${picked.length}, 1fr)`;
  });
}

// ---------------------------------------------- entity hero: top story
for (const slot of $$("[data-hero-news]")) {
  const hero = slot.closest(".hero-entity");
  strip("news", slot.dataset.heroNews).then(async (items) => {
    items = items.filter((i) => isHttp(i.url));
    if (!items.length) { slot.remove(); hero?.classList.add("no-news"); return; }
    slot.classList.remove("skeleton");
    const top = await firstLoading(items.filter((i) => isHttp(i.thumb)));
    if (top) {
      slot.href = top.url;
      slot.replaceChildren(
        el("div", { class: "news-card-img" }, img(top.thumb)),
        el("div", { class: "news-card-body" },
          el("span", { class: "news-card-src", text: top.host }),
          el("span", { class: "news-card-title", text: top.title }),
          top.ago ? el("span", { class: "news-card-age", text: top.ago }) : ""));
      return;
    }
    // No usable image: a compact "Top stories" list instead of one bare headline.
    const list = el("div", { class: "news-card news-card--list" },
      el("span", { class: "news-card-label", text: "Top stories" }),
      ...items.slice(0, 3).map((i) => el("a", { class: "news-line", href: i.url },
        el("span", { class: "news-card-src", text: [i.host, i.ago].filter(Boolean).join(" · ") }),
        el("span", { class: "news-line-title", text: i.title }))));
    slot.replaceWith(list);
  });
}

// ------------------------------------------ works (movies / books / albums)
for (const section of $$("[data-works-qid]")) {
  const { worksQid, worksRole } = section.dataset;
  getJSON(`/api/works?qid=${encodeURIComponent(worksQid)}&role=${encodeURIComponent(worksRole)}`).then((items) => {
    if (!items.length) return section.remove();
    $(".works-row", section).replaceChildren(...items.map((w) =>
      el("a", { class: "work", href: `/search?q=${encodeURIComponent(w.title)}` },
        el("span", { class: "work-title", text: w.title }),
        el("span", { class: "work-meta", text: [w.year, w.type].filter(Boolean).join(" · ") }))));
  });
}

// --------------------------------------- place tiles: live weather chip
for (const tile of $$("[data-weather-lat]")) {
  const { weatherLat, weatherLon } = tile.dataset;
  getJSON(`/api/weather?lat=${weatherLat}&lon=${weatherLon}`).then((w) => {
    if (w == null || w.temp == null) return tile.remove();
    $(".tile-value", tile).classList.remove("skeleton-text");
    $(".tile-value", tile).textContent = `${w.temp}° ${w.label}`;
    $(".tile-sub", tile).textContent = w.time ? `${w.time.time} local · ${w.time.diff}` : "";
  });
}

// ------------------------------------------------------- related searches
for (const section of $$("[data-related-q]")) {
  const q = section.dataset.relatedQ;
  getJSON(`/suggest?q=${encodeURIComponent(q)}`).then((data) => {
    const list = (Array.isArray(data) && Array.isArray(data[1]) ? data[1] : [])
      .filter((s) => typeof s === "string" && s.toLowerCase() !== q.toLowerCase()).slice(0, 8);
    if (!list.length) return;
    $(".chips", section).replaceChildren(...list.map((s) =>
      el("a", { class: "chip", href: `/search?q=${encodeURIComponent(s)}`, text: s })));
    section.hidden = false;
  });
}

// ------------------------------------------------------------ live clock
function tick() {
  for (const node of $$("[data-clock-tz]")) {
    node.textContent = new Intl.DateTimeFormat("en-GB", { timeZone: node.dataset.clockTz, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date());
  }
  for (const node of $$("[data-clock-date-tz]")) {
    node.textContent = new Intl.DateTimeFormat("en-GB", { timeZone: node.dataset.clockDateTz, weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(new Date());
  }
}
if ($("[data-clock-tz]")) { tick(); setInterval(tick, 1000); }

// ---------------------------------------------------------- converters
const fmt = (n) => (Number.isFinite(n) ? Number(n.toPrecision(10)).toLocaleString("en-US", { maximumFractionDigits: 6 }) : "");
const TEMP = {
  toK: { "°C": (v) => v + 273.15, "°F": (v) => (v - 32) * 5 / 9 + 273.15, K: (v) => v },
  fromK: { "°C": (v) => v - 273.15, "°F": (v) => (v - 273.15) * 9 / 5 + 32, K: (v) => v },
};
for (const hero of $$("[data-converter]")) {
  const input = $("[data-in]", hero), output = $("[data-out]", hero), text = $("[data-out-text]", hero);
  const { converter, rate, from, to } = hero.dataset;
  const forward = (v) => converter === "temp" ? TEMP.fromK[to](TEMP.toK[from](v)) : v * Number(rate);
  const backward = (v) => converter === "temp" ? TEMP.fromK[from](TEMP.toK[to](v)) : v / Number(rate);
  input.addEventListener("input", () => {
    const v = forward(parseFloat(input.value));
    output.value = Number.isFinite(v) ? Number(v.toPrecision(10)) : "";
    text.textContent = fmt(v);
  });
  output.addEventListener("input", () => {
    const v = backward(parseFloat(output.value));
    input.value = Number.isFinite(v) ? Number(v.toPrecision(10)) : "";
    text.textContent = fmt(parseFloat(output.value));
  });
}

// --------------------------------------------------------------- random
function randInt(min, max) {
  const span = max - min + 1;
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return min + (buf[0] % span);
}
for (const hero of $$("[data-random]")) {
  const out = $("[data-random-out]", hero);
  const roll = () => {
    const { random, min, max } = hero.dataset;
    out.textContent = random === "coin" ? (randInt(0, 1) ? "Heads" : "Tails")
      : String(randInt(Number(min), Number(max)));
  };
  roll();
  $("[data-random-again]", hero).addEventListener("click", roll);
}

// ------------------------------------------------------- places (lazy)
for (const hero of $$("[data-places-cat]")) {
  const { placesCat, placesArea, placesNear } = hero.dataset;
  const grid = $("[data-places-grid]", hero), status = $("[data-places-status]", hero);
  const url = `/api/places?cat=${encodeURIComponent(placesCat)}&area=${encodeURIComponent(placesArea)}&near=${placesNear === "1"}`;
  fetch(url).then((r) => r.json()).catch(() => ({ error: "Archer couldn't reach OpenStreetMap." })).then((data) => {
    if (data.error || !Array.isArray(data.places)) {
      grid.remove();
      status.textContent = (data.error || "Something went wrong.") + " The web results below still work.";
      status.classList.add("is-error");
      return;
    }
    if (isHttp(data.map)) $("[data-places-map]", hero).href = data.map;
    $("[data-places-kicker]", hero).textContent = `Within ${data.radius_km} km of ${data.area} · OpenStreetMap`;
    if (!data.places.length) {
      grid.remove();
      status.textContent = `OpenStreetMap has no named ${data.label.toLowerCase()} mapped here yet.`;
      return;
    }
    if (data.source === "photon") {
      status.textContent = "Quick list: OpenStreetMap's detailed server is busy, so opening hours and websites are missing. Search again in a few minutes for the full version.";
      status.classList.remove("is-error");
    } else {
      status.remove();
    }
    grid.replaceChildren(...data.places.map((p) => {
      const actions = el("div", { class: "place-actions" },
        isHttp(p.website) ? el("a", { class: "chip", href: p.website, text: "Website" }) : null,
        p.phone ? el("a", { class: "chip", href: `tel:${p.phone.replace(/[^+\d]/g, "")}`, text: "Call" }) : null,
        isHttp(p.osm) ? el("a", { class: "chip", href: p.osm, text: "Map" }) : null);
      return el("article", { class: "place" },
        el("div", { class: "place-top" },
          el("h3", { class: "place-name" }, el("a", { href: isHttp(p.osm) ? p.osm : "#", text: p.name })),
          el("span", { class: "place-dist", text: p.distance })),
        p.kind ? el("p", { class: "place-kind" }, icon("tag"), document.createTextNode(p.kind)) : null,
        p.address ? el("p", { class: "place-line" }, icon("pin"), document.createTextNode(p.address)) : null,
        p.hours ? el("p", { class: "place-line place-hours", title: "OpenStreetMap opening_hours" }, icon("clock"), document.createTextNode(p.hours)) : null,
        actions);
    }));
  });
}

// ---------------------------------------------------- timer + stopwatch
function beep(ctx, freq = 880, length = 0.12, when = 0) {
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.3, ctx.currentTime + when);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + when + length);
  osc.connect(gain).connect(ctx.destination);
  osc.start(ctx.currentTime + when);
  osc.stop(ctx.currentTime + when + length);
}
const pad = (n) => String(Math.floor(n)).padStart(2, "0");
const clockText = (s) => (s >= 3600 ? `${pad(s / 3600)}:` : "") + `${pad((s % 3600) / 60)}:${pad(s % 60)}`;

for (const hero of $$("[data-timer]")) {
  const display = $("[data-timer-display]", hero), startBtn = $("[data-timer-start]", hero);
  let mode = hero.dataset.timer, preset = Number(hero.dataset.seconds) || 300;
  let running = false, started = 0, banked = 0, raf = 0, audio = null;
  const elapsed = () => banked + (running ? (performance.now() - started) / 1000 : 0);
  const render = () => {
    const t = mode === "timer" ? Math.max(preset - elapsed(), 0) : elapsed();
    display.textContent = mode === "stopwatch" && t < 3600 ? `${clockText(t)}.${String(Math.floor((t % 1) * 10))}` : clockText(Math.ceil(t));
    if (mode === "timer" && running && t <= 0) {
      running = false; banked = preset; startBtn.textContent = "Start";
      display.classList.add("is-done");
      if (audio) [0, 0.3, 0.6].forEach((w) => beep(audio, 880, 0.18, w));
      document.title = "⏰ Time's up · Archer";
      return;
    }
    if (running) raf = requestAnimationFrame(render);
  };
  const reset = () => { running = false; banked = 0; cancelAnimationFrame(raf); startBtn.textContent = "Start"; display.classList.remove("is-done"); render(); };
  startBtn.addEventListener("click", () => {
    audio ??= new AudioContext();  // created on a user gesture so the alarm can play
    if (running) { banked = elapsed(); running = false; startBtn.textContent = "Resume"; return; }
    if (mode === "timer" && elapsed() >= preset) reset();
    started = performance.now(); running = true; startBtn.textContent = "Pause"; render();
  });
  $("[data-timer-reset]", hero).addEventListener("click", reset);
  for (const b of $$("[data-timer-set]", hero)) b.addEventListener("click", () => { preset = Number(b.dataset.timerSet); mode = "timer"; sync(); reset(); });
  const sync = () => {
    for (const b of $$("[data-timer-mode]", hero)) b.classList.toggle("is-active", b.dataset.timerMode === mode);
    $("[data-timer-presets]", hero).hidden = mode !== "timer";
  };
  for (const b of $$("[data-timer-mode]", hero)) b.addEventListener("click", () => { mode = b.dataset.timerMode; sync(); reset(); });
  sync(); render();
}

// ------------------------------------------------------------- metronome
for (const hero of $$("[data-metronome]")) {
  const slider = $("[data-bpm-in]", hero), out = $("[data-bpm-out]", hero), toggle = $("[data-metronome-toggle]", hero);
  const dots = $$(".beats span", hero);
  let bpm = Number(hero.dataset.bpm), ctx = null, timer = 0, beat = 0, next = 0;
  const set = (v) => { bpm = Math.min(Math.max(v, 30), 280); slider.value = bpm; out.textContent = bpm; };
  const schedule = () => {
    // Look-ahead scheduling on the audio clock keeps the beat steady even if the page is busy.
    while (next < ctx.currentTime + 0.1) {
      const accent = beat % 4 === 0, index = beat % 4, delay = Math.max(next - ctx.currentTime, 0);
      beep(ctx, accent ? 1320 : 880, 0.05, delay);
      setTimeout(() => dots.forEach((d, i) => d.classList.toggle("is-on", i === index)), delay * 1000);
      next += 60 / bpm; beat += 1;
    }
  };
  toggle.addEventListener("click", () => {
    ctx ??= new AudioContext();
    if (timer) { clearInterval(timer); timer = 0; toggle.textContent = "Play"; dots.forEach((d) => d.classList.remove("is-on")); return; }
    beat = 0; next = ctx.currentTime + 0.05; timer = setInterval(schedule, 25); toggle.textContent = "Stop";
  });
  slider.addEventListener("input", () => set(Number(slider.value)));
  for (const b of $$("[data-bpm-step]", hero)) b.addEventListener("click", () => set(bpm + Number(b.dataset.bpmStep)));
}

// ------------------------------------------------- EMI / BMI / tip calculators
const money = (n) => (Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : "—");
const FINANCE = {
  emi: ({ principal, rate, years }) => {
    const n = Math.round(years * 12), r = rate / 1200;
    const emi = r ? (principal * r * (1 + r) ** n) / ((1 + r) ** n - 1) : principal / n;
    return { emi: money(emi), interest: money(emi * n - principal), total: money(emi * n) };
  },
  bmi: ({ height, weight }) => {
    const m = height / 100, bmi = weight / (m * m);
    const category = bmi < 18.5 ? "Underweight" : bmi < 25 ? "Healthy" : bmi < 30 ? "Overweight" : "Obese";
    return { bmi: Number.isFinite(bmi) ? bmi.toFixed(1) : "—", category, range: `${(18.5 * m * m).toFixed(1)}–${(24.9 * m * m).toFixed(1)} kg` };
  },
  tip: ({ bill, tip, people }) => {
    const tipAmount = (bill * tip) / 100, total = bill + tipAmount;
    return { tipAmount: money(tipAmount), total: money(total), each: money(total / Math.max(people, 1)) };
  },
};
for (const hero of $$("[data-finance]")) {
  const calc = FINANCE[hero.dataset.finance];
  const update = () => {
    const values = Object.fromEntries($$("[data-fin]", hero).map((i) => [i.dataset.fin, parseFloat(i.value) || 0]));
    for (const [key, value] of Object.entries(calc(values))) {
      const node = $(`[data-fin-out="${key}"]`, hero);
      if (node) node.textContent = value;
    }
  };
  hero.addEventListener("input", update);
  update();
}

// ------------------------------------------------------- copy + audio
for (const btn of $$("[data-copy]")) {
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(btn.dataset.copy); btn.classList.add("is-done"); setTimeout(() => btn.classList.remove("is-done"), 1200); } catch {}
  });
}
for (const btn of $$("[data-audio]")) {
  btn.addEventListener("click", () => { if (isHttp(btn.dataset.audio)) new Audio(btn.dataset.audio).play().catch(() => {}); });
}

// ------------------------------------------------ image viewer (side panel)
// Clicking a tile opens a panel beside the grid (large image, source, similar images) instead
// of leaving the page. Modified clicks and no-JS still follow the tile's link to the source page.
const viewer = $("[data-image-viewer]");
if (viewer) {
  const panel = $(".image-panel", viewer);
  const ip = (name) => $(`[data-ip="${name}"]`, panel);
  const tiles = $$(".image-tile", viewer);
  const stageImg = ip("img");
  let index = -1, token = 0;

  // Masonry: each tile takes its image's aspect ratio and joins the shortest column, which keeps
  // the relevance order roughly left-to-right. Columns are only re-dealt when their count changes;
  // a late-loading image just resizes its own tile in place.
  const grid = $(".image-grid", viewer);
  const ratios = new Map();
  const clampRatio = (w, h) => Math.min(2, Math.max(0.5, w / h));
  const sizeOf = (res) => { const m = /(\d+)\D+(\d+)/.exec(res || ""); return m && +m[1] && +m[2] ? clampRatio(+m[1], +m[2]) : 1; };
  const setRatio = (tile, r) => { ratios.set(tile, r); tile.style.setProperty("--r", r.toFixed(3)); };
  let columns = 0;
  function deal() {
    const MIN = 200, GAP = 10;
    const n = Math.max(2, Math.floor((grid.clientWidth + GAP) / (MIN + GAP)));
    if (n === columns || !tiles.length) return;
    columns = n;
    const cols = Array.from({ length: n }, () => el("div", { class: "image-col" }));
    const heights = new Array(n).fill(0);
    for (const tile of tiles) {
      const c = heights.indexOf(Math.min(...heights));
      cols[c].append(tile);
      heights[c] += 1 / ratios.get(tile);
    }
    grid.replaceChildren(...cols);
  }
  if (tiles.length) {
    grid.classList.add("is-masonry");
    for (const tile of tiles) {
      setRatio(tile, sizeOf(tile.dataset.res));
      const picture = $("img", tile);
      const fit = () => { if (picture.naturalWidth) setRatio(tile, clampRatio(picture.naturalWidth, picture.naturalHeight)); };
      if (picture.complete) fit(); else picture.addEventListener("load", fit);
      picture.addEventListener("error", () => tile.remove());
    }
    deal();
    new ResizeObserver(deal).observe(grid);
  }

  const itemOf = (t) => ({ full: t.dataset.full, thumb: t.dataset.thumb, title: t.dataset.title, url: t.href,
                           source: t.dataset.source, res: t.dataset.res });

  function setLink(node, href) {
    if (isHttp(href)) { node.href = href; node.removeAttribute("aria-disabled"); }
    else node.removeAttribute("href");
  }

  function renderSimilar(item) {
    const box = ip("similar");
    box.replaceChildren(el("p", { class: "ip-empty", text: "Finding similar images…" }));
    const mine = ++token;
    const url = `/api/similar?q=${encodeURIComponent(viewer.dataset.q)}&title=${encodeURIComponent(item.title || "")}&exclude=${encodeURIComponent(item.full || item.thumb || "")}`;
    getJSON(url).then((list) => {
      if (mine !== token) return;  // a newer image was picked meanwhile
      const buttons = list.filter((s) => isHttp(s.thumb)).map((s) => {
        const b = el("button", { type: "button", title: s.title });
        const picture = img(s.thumb, s.title);
        picture.addEventListener("error", () => b.remove());
        b.append(picture);
        b.addEventListener("click", () => show({ full: s.full, thumb: s.thumb, title: s.title, url: s.url, source: s.source, res: s.res }, -1));
        return b;
      });
      box.replaceChildren(...(buttons.length ? buttons : [el("p", { class: "ip-empty", text: "No similar images found." })]));
    });
  }

  function show(item, at) {
    index = at;
    tiles.forEach((t, i) => t.classList.toggle("is-active", i === at));
    if (at >= 0) tiles[at].scrollIntoView({ block: "nearest" });
    viewer.classList.add("has-panel");
    panel.hidden = false;
    panel.scrollTop = 0;

    stageImg.src = item.thumb || item.full;  // instant preview, upgraded once the full image decodes
    stageImg.alt = item.title || "";
    if (isHttp(item.full) && item.full !== item.thumb) {
      const probe = new Image();
      probe.referrerPolicy = "no-referrer";
      probe.onload = () => { if (index === at && stageImg.alt === (item.title || "")) stageImg.src = item.full; };
      probe.src = item.full;
    }
    ip("title").textContent = item.title || "Untitled image";
    ip("source").textContent = item.source || "";
    ip("res").textContent = item.res || "";
    setLink(ip("visit"), item.url);
    setLink(ip("open"), item.full || item.thumb);
    setLink(ip("stage"), item.full || item.url);
    ip("prev").disabled = at <= 0;
    ip("next").disabled = at < 0 || at >= tiles.length - 1;
    renderSimilar(item);
  }

  function close() {
    token++; index = -1;
    panel.hidden = true;
    viewer.classList.remove("has-panel");
    tiles.forEach((t) => t.classList.remove("is-active"));
  }

  const step = (d) => {
    let n = index + d;
    while (index >= 0 && tiles[n] && !tiles[n].isConnected) n += d;  // skip tiles dropped for a broken image
    if (index >= 0 && tiles[n]) show(itemOf(tiles[n]), n);
  };

  tiles.forEach((tile, i) => tile.addEventListener("click", (e) => {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    show(itemOf(tile), i);
  }));
  ip("close").addEventListener("click", close);
  ip("prev").addEventListener("click", () => step(-1));
  ip("next").addEventListener("click", () => step(1));
  document.addEventListener("keydown", (e) => {
    if (panel.hidden || /input|textarea/i.test(document.activeElement.tagName)) return;
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
  });
}
