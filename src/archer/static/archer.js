// Archer results page: lazy modules and small interactive widgets.
// Everything is built with DOM APIs + textContent; no HTML from the network is injected.
"use strict";

// AI uses the existing search page; only an authorized POST can begin generation.
// One panel serves both levels: Balanced renders a single answer card in the results column,
// AI Mode (body.ai-fullview) turns the same panel into a conversation with a source rail.
document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("#ai-search");
  const modes = [...document.querySelectorAll("[data-ai-mode]")];
  if (!root) {
    modes.filter(b => b.dataset.aiMode !== "classic").forEach(b => { b.disabled = true; b.title = "AI is available on the first page of All results"; });
    return;
  }
  const find = selector => root.querySelector(selector);
  const workspace = find("[data-ai-workspace]");
  const thread = find("[data-ai-thread]");
  const welcome = find("[data-ai-welcome]");
  const turnTemplate = find("[data-ai-turn-template]");
  const prompt = find("[data-ai-prompt]");
  const settingsPanel = find("[data-ai-settings-panel]");
  const settingsStatus = find("[data-ai-settings-status]");
  const sourcesPanel = find("[data-ai-sources]");
  const sources = find("[data-ai-source-list]");
  const notices = find("[data-ai-notices]");
  const compareBar = find("[data-ai-compare-bar]");
  const compareButton = find("[data-ai-compare]");
  const selectButton = find("[data-ai-select]");
  const followup = find("[data-ai-followup]");
  const composerInput = followup.elements.question;
  const sendButton = followup.querySelector('[type="submit"]');
  const header = document.querySelector(".top");
  const footer = document.querySelector("[data-ai-footer]");
  const configured = root.dataset.configured === "true";
  let csrf = root.dataset.csrf;
  let consent = root.dataset.consent === "true";
  let defaultMode = root.dataset.default;
  let runId = "", busy = false, controller = null, version = 0, pending = null;
  let turn = null;
  const sourceMap = new Map([...sources.children].map(n => [n.dataset.sourceId,
    { id: n.dataset.sourceId, title: n.querySelector("a, strong").textContent.trim(), url: n.dataset.sourceUrl, kind: n.dataset.sourceKind }]));
  const archerMark = () => {
    const mark = find(".ai-nav-rail .pxmark").cloneNode(true);
    mark.setAttribute("width", "14"); mark.setAttribute("height", "14");
    return mark;
  };
  // Balanced can shrink to a short preview; the choice is a per-browser convenience.
  let small = (() => { try { return localStorage.getItem("archer-ai-small") !== "0"; } catch { return true; } })();
  const applySize = () => {
    root.classList.toggle("is-small", small);
    thread.querySelectorAll("[data-turn-size]").forEach(button => {
      button.setAttribute("aria-expanded", String(!small));
      button.setAttribute("aria-label", small ? "Expand AI answer" : "Make AI answer smaller");
      button.title = small ? "Expand answer" : "Smaller answer";
    });
  };
  const setSmall = value => {
    small = value;
    try { localStorage.setItem("archer-ai-small", value ? "1" : "0"); } catch {}
    applySize();
  };
  // Images arrive from the same cached strip the results page uses, so they show before the text does.
  const loadVisuals = t => {
    if (!root.dataset.query || t.extra.question || t.extra.selected) return;
    strip("images", root.dataset.query).then(items => {
      const row = t.part("visuals");
      if (!row.isConnected || t.node.classList.contains("has-media")) return;
      const tiles = items.filter(i => isHttp(i.url) && isHttp(i.thumb)).slice(0, 8).map(item => {
        const picture = img(item.thumb, item.title);
        if (!picture) return null;
        const tile = el("a", { class: "ai-visual", href: item.url, target: "_blank", rel: "noopener noreferrer", title: item.title + (item.source ? " · " + item.source : "") }, picture);
        picture.addEventListener("error", () => tile.remove());
        return tile;
      }).filter(Boolean);
      if (tiles.length < 3) return;
      row.replaceChildren(...tiles);
      row.hidden = false;
    }).catch(() => {});
  };
  const isFull = () => document.body.classList.contains("ai-fullview");
  const turns = () => [...thread.querySelectorAll(".ai-turn")];
  const answered = () => turns().some(n => n.classList.contains("has-answer"));

  const request = async (path, data, options = {}) => {
    const response = await fetch("/api/ai/" + path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Archer-CSRF": csrf }, body: JSON.stringify(data), ...options });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === "string" ? error.detail : "Archer couldn't complete that request.");
    }
    return response;
  };
  const safeLink = (url, text) => isHttp(url) ? el("a", { href: url, text, target: "_blank", rel: "noopener noreferrer" }) : el("span", { text });
  const hostOf = url => { try { return new URL(url).hostname; } catch { return ""; } };

  // Same letter badge + favicon as the web results (macros.html site_badge, app.py _site_name/_hue).
  const crc32 = text => {
    let crc = ~0;
    for (const byte of new TextEncoder().encode(text)) {
      crc ^= byte;
      for (let k = 0; k < 8; k++) crc = crc & 1 ? (crc >>> 1) ^ 0xEDB88320 : crc >>> 1;
    }
    return ~crc >>> 0;
  };
  const SECOND_LEVEL = new Set(["co", "com", "org", "net", "ac", "gov", "edu", "nic", "gen", "ne", "or"]);
  const siteName = host => {
    const parts = host.split(".");
    const name = parts.length >= 3 && SECOND_LEVEL.has(parts.at(-2)) ? parts.at(-3) : parts.length >= 2 ? parts.at(-2) : host;
    return name.slice(0, 1).toUpperCase() + name.slice(1);
  };
  const siteBadge = host => {
    const name = siteName(host);
    const badge = el("span", { class: "badge" }, (name[0] || "?").toUpperCase());
    badge.style.setProperty("--h", crc32(name) % 360);
    const icon = el("img", { class: "badge-icon", src: "/api/favicon/" + host, alt: "", loading: "lazy" });
    badge.append(icon);
    wireFavicon(icon);
    return badge;
  };

  const mode = value => modes.forEach(b => {
    const active = b.dataset.aiMode === value;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-pressed", String(active));
  });
  const setFooter = text => { if (footer) footer.textContent = text; };

  // ------------------------------------------------------------ Classic prompt
  const updatePrompt = () => {
    const resumable = answered() || busy;
    const show = !isFull() && workspace.hidden && settingsPanel.hidden && (resumable || root.dataset.eligible === "true");
    prompt.hidden = !show;
    if (!show) return;
    const research = turns().some(n => n.dataset.depth === "research");
    find("[data-ai-prompt-text]").textContent = busy && research ? "Archer is still researching this question."
      : resumable ? (research ? "Your AI Mode conversation is still here." : "Your AI answer is still here.")
      : "Archer can answer this from these results.";
    find("[data-ai-ask]").textContent = resumable && !research ? "Show answer" : "Quick answer";
    find("[data-ai-ask]").hidden = resumable && research;
    find("[data-ai-research]").firstChild.textContent = resumable && research ? "Return to AI Mode " : "AI Mode ";
  };

  // ------------------------------------------------------------ settings and consent
  const openSettings = (reason = "") => {
    settingsPanel.hidden = false;
    prompt.hidden = true;
    const title = find("[data-ai-settings-title]");
    const save = find("[data-ai-save]");
    if (title) title.textContent = pending ? "Turn on AI answers?" : "AI answers, on your terms";
    if (save) save.textContent = pending ? "Turn on and continue" : "Save";
    if (pending && find("[data-ai-consent]")) find("[data-ai-consent]").checked = true;
    if (settingsStatus) settingsStatus.textContent = reason;
    (find("[data-ai-consent]") || find("[data-ai-settings-close]")).focus({ preventScroll: true });
    if (!isFull()) settingsPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const closeSettings = () => {
    settingsPanel.hidden = true;
    if (pending) {
      pending = null;
      if (!busy && !answered()) mode(isFull() ? "research" : "classic");
    }
    updatePrompt();
  };

  // ------------------------------------------------------------ views
  const enterFullView = (push = true) => {
    document.body.classList.add("ai-fullview");
    workspace.hidden = false;
    followup.hidden = false;
    prompt.hidden = true;
    sourcesPanel.open = true;
    welcome.hidden = turns().length > 0;
    mode("research");
    if (push && !location.pathname.startsWith("/ai")) history.pushState({ archerAI: true }, "", "/ai?q=" + encodeURIComponent(root.dataset.query));
  };
  // Leaving AI Mode only changes the view: research keeps running and can be resumed. Classic stops it.
  const leaveFullView = () => {
    document.body.classList.remove("ai-fullview", "ai-sources-open");
    if (location.pathname.startsWith("/ai")) history.replaceState(null, "", "/search?q=" + encodeURIComponent(root.dataset.query));
    workspace.hidden = true;
    sourcesPanel.open = false;
    if (!answered() && !busy) mode("classic");
    updatePrompt();
  };
  const hideAnswer = async () => {
    await stop();
    workspace.hidden = true;
    mode("classic");
    setFooter("Classic mode · AI answer hidden");
    updatePrompt();
  };

  // ------------------------------------------------------------ sources
  const notice = text => {
    if (![...notices.children].some(n => n.textContent === text)) notices.append(el("p", { text }));
  };
  const revealSource = id => {
    sourcesPanel.open = true;
    document.body.classList.add("ai-sources-open");
    const row = [...sources.children].find(n => n.dataset.sourceId === id);
    if (!row) return;
    row.classList.remove("is-target");
    void row.offsetWidth;
    row.classList.add("is-target");
    row.tabIndex = -1;
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    row.focus({ preventScroll: true });
  };
  const updateCompare = () => {
    const count = sources.querySelectorAll("input:checked").length;
    compareButton.disabled = count < 2 || count > 4;
    find("[data-ai-compare-count]").textContent = count < 2 ? "Pick two to four sources" : count > 4 ? "Four at most" : count + " selected";
  };
  const setSelecting = on => {
    sourcesPanel.classList.toggle("is-selecting", on);
    selectButton.setAttribute("aria-pressed", String(on));
    selectButton.textContent = on ? "Cancel" : "Select to compare";
    compareBar.hidden = !on;
    if (!on) sources.querySelectorAll("input:checked").forEach(n => { n.checked = false; });
    updateCompare();
  };
  const renderSources = items => {
    const selected = new Set([...sources.querySelectorAll("input:checked")].map(n => n.value));
    sources.replaceChildren();
    sourceMap.clear();
    const peek = find("[data-ai-sources-peek]");
    peek.replaceChildren();
    for (const source of items) {
      sourceMap.set(source.id, source);
      const host = hostOf(source.url);
      const check = el("input", { type: "checkbox", value: source.id, "aria-label": "Select " + source.title + " for comparison" });
      check.checked = selected.has(source.id);
      const site = host
        ? el("span", { class: "ai-source-site" }, siteBadge(host), host.replace(/^www\./, ""))
        : el("span", { class: "ai-source-site" }, el("span", { class: "badge ai-badge-archer" }, archerMark()), "Archer " + source.kind);
      if (source.published) site.append(el("span", { class: "ai-source-date", text: source.published.slice(0, 10) }));
      site.append(el("span", { class: "ai-source-id", text: source.id }));
      const body = el("div", { class: "ai-source-body" }, site, host ? safeLink(source.url, source.title) : el("strong", { text: source.title }));
      // Tool and card evidence is structured data; its card in the answer is the readable form.
      if (source.text && host) body.append(el("p", { class: "ai-source-excerpt", text: source.text.length > 220 ? source.text.slice(0, 220).trim() + "…" : source.text }));
      if (source.limitation) body.append(el("small", { class: "ai-source-limit", text: source.limitation }));
      const row = el("div", { class: "ai-source", "data-source-id": source.id }, el("label", { class: "ai-source-pick" }, check), body);
      const thumb = img(source.thumbnail, "");
      if (thumb) row.append(el("span", { class: "ai-source-thumb" }, thumb));
      sources.append(row);
      if (host && peek.children.length < 4) peek.append(siteBadge(host));
    }
    find("[data-ai-source-count]").textContent = String(items.length);
    find("[data-ai-top-count]").textContent = String(items.length);
    updateCompare();
  };
  const citations = ids => {
    const group = el("span", { class: "ai-citations" });
    const id = (ids || []).find(i => sourceMap.has(i));
    if (!id) return group;
    const source = sourceMap.get(id);
    const label = source.url ? siteName(hostOf(source.url)) : "Archer";
    const extra = ids.length > 1 ? " +" + (ids.length - 1) : "";
    const button = el("button", { type: "button", class: "ai-citation-chip", text: label + extra,
      title: ids.map(i => sourceMap.get(i)?.title || i).join("\n"), "aria-label": "Show source " + id + ": " + source.title });
    button.addEventListener("click", () => revealSource(id));
    group.append(button);
    return group;
  };

  // ------------------------------------------------------------ answer rendering
  const formatted = text => {
    const fragment = document.createDocumentFragment();
    for (const part of text.split(/(\*\*[^*]+\*\*)/g)) fragment.append(part.startsWith("**") && part.endsWith("**") ? el("strong", { text: part.slice(2, -2) }) : document.createTextNode(part));
    return fragment;
  };
  const renderBlock = block => {
    const section = el("section", { class: "ai-block ai-block--" + block.kind });
    if (block.kind === "inference") section.append(el("span", { class: "ai-block-label", text: "Archer's take" }));
    if (block.kind === "limitation") section.append(el("span", { class: "ai-block-label", text: "What remains uncertain" }));
    if (block.heading) section.append(el("h3", { text: block.heading }));
    if (block.kind === "table") {
      const table = el("table");
      table.append(el("thead", {}, el("tr", {}, ...block.columns.map(t => el("th", { scope: "col", text: t })))));
      const body = el("tbody");
      for (const row of block.rows) body.append(el("tr", {}, ...row.map((cell, index) => el(index ? "td" : "th", index ? {} : { scope: "row" }, formatted(cell.text), citations(cell.citations)))));
      table.append(body);
      section.append(el("div", { class: "ai-table-scroll", tabindex: "0", role: "region", "aria-label": block.heading || "Comparison" }, table));
    } else if (block.kind === "list" || block.kind === "steps") {
      section.append(el(block.kind === "steps" ? "ol" : "ul", {}, ...block.items.map(item => el("li", {}, formatted(item.text), citations(item.citations)))));
    } else {
      section.append(el("p", {}, formatted(block.text), citations(block.citations)));
    }
    // The lead paragraph goes above any tool cards; everything after it follows the cards.
    if (!turn.blocks.length) turn.cards.before(section);
    else turn.answer.append(section);
    turn.blocks.push(block);
    turn.node.classList.add("has-answer");
  };
  const fact = (label, value) => el("div", { class: "ai-fact" }, el("small", { text: label }), el("strong", { text: String(value) }));
  const renderCard = card => {
    if (turn.shown.has(card.source_id)) return;
    turn.shown.add(card.source_id);
    const data = card.data || {};
    const panel = el("article", { class: "ai-tool-card ai-tool-card--" + card.kind },
      el("p", { class: "ai-tool-label", text: card.kind.replace("_", " ") }), el("h3", { text: card.title }));
    if (card.kind === "media") {
      // A gallery chosen for the question replaces the generic image row.
      turn.node.classList.add("has-media");
      turn.part("visuals").hidden = true;
      const gallery = el("div", { class: "ai-media-grid" + (data.category === "videos" ? " ai-media-grid--videos" : "") });
      for (const item of data.items || []) {
        const tile = safeLink(item.url, "");
        tile.classList.add("ai-media-tile");
        const preview = img(item.thumbnail, item.title);
        if (preview) tile.append(preview);
        else tile.classList.add("ai-media-missing");
        tile.append(el("span", { text: item.title }), el("small", { text: item.host + (item.length ? " · " + item.length : "") }));
        gallery.append(tile);
      }
      panel.append(gallery);
    } else if (card.kind === "weather") {
      panel.append(el("p", { class: "ai-weather-now", text: data.temp + "° · " + data.label }), el("small", { text: data.place + " · " + data.timezone }));
      const hours = (data.forecast_hours || []).filter(h => h.time >= (data.observed_at || "")).slice(0, 48);
      const chart = el("div", { class: "ai-forecast" });
      for (const hour of hours.filter((h, i) => i % 3 === 0)) {
        const column = el("div", { class: "ai-forecast-hour", title: hour.time + " · " + hour.temp + "°C · " + hour.rain + "% rain" }, el("strong", { text: hour.temp + "°" }));
        const bar = el("span", { class: "ai-forecast-bar" });
        bar.style.height = Math.max(4, Math.min(48, Number(hour.rain) / 2 + 4)) + "px";
        column.append(bar, el("small", { text: hour.time.slice(11, 16) }), el("small", { class: "ai-forecast-rain", text: hour.rain + "%" }));
        chart.append(column);
      }
      panel.append(chart, el("p", { class: "ai-fine", text: "°C · bars show chance of rain · Open-Meteo" }));
    } else if (card.kind === "places") {
      for (const place of (data.places || []).slice(0, 6)) {
        const row = el("div", { class: "ai-place" }, safeLink(place.osm, place.name), el("small", { text: [place.distance, place.address, place.hours && "Listed hours: " + place.hours].filter(Boolean).join(" · ") }));
        if (place.website) row.append(safeLink(place.website, "Website ↗"));
        panel.append(row);
      }
      panel.append(safeLink(data.map, "Open map ↗"), el("p", { class: "ai-fine", text: "OpenStreetMap listings · no ratings or reviews · distance from the area centre" }));
    } else if (card.kind === "entity") {
      if (data.description) panel.append(el("p", { text: data.description }));
      const grid = el("div", { class: "ai-facts" });
      for (const tile of (data.tiles || []).slice(0, 6)) grid.append(fact(tile.label, tile.value));
      panel.append(grid);
    } else if (card.kind === "watch") {
      for (const group of data.groups || []) panel.append(el("p", { text: group.label + ": " + group.providers.map(p => p.name).join(", ") }));
      if (!data.groups?.length) panel.append(el("p", { text: "No providers listed for this region." }));
      panel.append(safeLink(data.link, "Check viewing options ↗"), el("small", { text: "Country: " + (data.country || "configured region") + " · JustWatch via TMDB" }));
    } else if (card.kind === "calculation") {
      const grid = el("div", { class: "ai-facts" });
      if (data.monthly_payment != null) {
        const format = value => Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
        for (const [key, label] of [["monthly_payment", "Monthly payment"], ["total_interest", "Total interest"], ["total_payment", "Total repaid"]]) grid.append(fact(label, format(data[key])));
        const note = el("p", { class: "ai-fine", text: data.assumptions });
        panel.append(grid, note);
        const edit = el("form", { class: "ai-loan-edit" });
        for (const [key, label] of [["principal", "Principal"], ["annual_rate", "Rate %"], ["months", "Months"]]) {
          edit.append(el("label", {}, document.createTextNode(label), el("input", { type: "number", name: key, value: data[key], min: key === "annual_rate" ? "0" : "1", max: key === "principal" ? "1000000000000" : key === "months" ? "1200" : "100", step: key === "months" ? "1" : "any", required: true })));
        }
        edit.append(el("button", { type: "submit", class: "ai-button ai-button--small", text: "Recalculate" }));
        edit.addEventListener("submit", event => {
          event.preventDefault();
          const values = Object.fromEntries(new FormData(edit));
          const p = Number(values.principal), rate = Number(values.annual_rate) / 1200, n = Number(values.months);
          const payment = rate ? p * rate / (-Math.expm1(-n * Math.log1p(rate))) : p / n;
          grid.replaceChildren(fact("Monthly payment", format(payment)), fact("Total interest", format(payment * n - p)), fact("Total repaid", format(payment * n)));
          panel.querySelector("h3").textContent = "Monthly loan payment · " + n + " months";
          panel.classList.add("is-edited");
          note.textContent = "Recalculated here, without AI. The written answer still describes the original inputs.";
        });
        panel.append(edit);
      } else panel.append(fact("Result", data.value ?? data.result ?? "See source evidence"));
    }
    panel.append(citations([card.source_id]));
    turn.cards.append(panel);
  };

  // ------------------------------------------------------------ turns
  const compareLabel = ids => "Compare " + ids.map(id => sourceMap.get(id)?.title || id).map(t => t.length > 48 ? t.slice(0, 46) + "…" : t).join(" vs ");
  const newTurn = (target, question, extra) => {
    const node = turnTemplate.content.firstElementChild.cloneNode(true);
    const part = name => node.querySelector("[data-turn-" + name + "]");
    const t = { node, part, depth: target, extra, question, blocks: [], shown: new Set(), steps: [],
      answer: part("answer"), cards: part("cards"), status: part("status") };
    node.dataset.depth = target;
    part("query").textContent = extra.selected ? compareLabel(extra.selected) : question;
    part("label").textContent = extra.selected ? "Comparison" : target === "research" ? "Research" : isFull() ? "Quick answer" : "AI answer";
    part("stop").addEventListener("click", () => stop());
    part("close").addEventListener("click", hideAnswer);
    part("size").addEventListener("click", () => setSmall(!small));
    part("more").addEventListener("click", () => setSmall(false));
    part("settings").addEventListener("click", () => settingsPanel.hidden ? openSettings() : closeSettings());
    part("expand").addEventListener("click", () => start("research", { parent: t.runId }));
    const retry = () => start(t.depth, { ...t.extra, regenerate: true, replace: true });
    part("retry").addEventListener("click", retry);
    part("retry-error").addEventListener("click", retry);
    part("copy").addEventListener("click", () => copyAnswer(t));
    welcome.hidden = true;
    thread.append(node);
    applySize();
    loadVisuals(t);
    return t;
  };
  const step = (text, stage) => {
    if (!turn || turn.steps.at(-1) === text) return;
    const list = turn.part("step-list");
    list.lastElementChild?.classList.add("is-done");
    list.append(el("li", { "data-stage": stage, text }));
    turn.steps.push(text);
    turn.part("steps-summary").textContent = "Working · step " + turn.steps.length;
    if (turn.depth === "research") {
      turn.part("steps").hidden = false;
      if (turn.steps.length === 1 && busy) turn.part("steps").open = true;
    }
  };
  const metaText = data => {
    const parts = [data.evidence_label];
    if (data.pages_read) parts.push(data.pages_read + (data.pages_read === 1 ? " page read" : " pages read"));
    if (data.searches) parts.push(data.searches + (data.searches === 1 ? " extra search" : " extra searches"));
    if (data.cost_usd != null) parts.push((data.cost_is_ceiling ? "≤ $" : "~$") + data.cost_usd.toFixed(data.cost_usd < 0.01 ? 4 : 2));
    return parts.filter(Boolean).join(" · ");
  };
  const copyAnswer = async t => {
    const lines = [];
    for (const block of t.blocks) {
      if (block.heading) lines.push("## " + block.heading);
      if (block.kind === "table") {
        lines.push("| " + block.columns.join(" | ") + " |", "|" + block.columns.map(() => " --- |").join(""));
        for (const row of block.rows) lines.push("| " + row.map(c => c.text).join(" | ") + " |");
      } else if (block.items) block.items.forEach((item, i) => lines.push((block.kind === "steps" ? i + 1 + "." : "-") + " " + item.text));
      else lines.push(block.text);
      lines.push("");
    }
    const cited = [...new Set(t.blocks.flatMap(b => [b.citations || [], ...(b.items || []).map(i => i.citations || []), ...(b.rows || []).flat().map(c => c.citations || [])].flat()))];
    const links = cited.map(id => sourceMap.get(id)).filter(s => s && s.url).map(s => "- " + s.title + " " + s.url);
    if (links.length) lines.push("Sources:", ...links);
    const button = t.part("copy");
    try {
      await navigator.clipboard.writeText(lines.join("\n").trim());
      button.classList.add("is-done");
      button.setAttribute("aria-label", "Copied");
      setTimeout(() => { button.classList.remove("is-done"); button.setAttribute("aria-label", "Copy answer"); }, 1600);
    } catch { button.title = "Copy isn't available here"; }
  };

  const setBusy = value => {
    busy = value;
    root.setAttribute("aria-busy", String(value));
    turn?.node.setAttribute("aria-busy", String(value));
    if (header && !isFull()) header.classList.toggle("is-busy", value);
    if (!value) header?.classList.remove("is-busy");
    followup.classList.toggle("is-busy", value);
    sendButton.setAttribute("aria-label", value ? "Stop answering" : "Send follow-up");
    syncComposer();
  };
  const syncComposer = () => { sendButton.disabled = !busy && !composerInput.value.trim(); };

  const finish = (success, data = {}) => {
    if (!turn) return;
    setBusy(false);
    root.dataset.stage = success ? "complete" : "error";
    turn.node.dataset.state = success ? "complete" : "error";
    const foot = turn.part("foot");
    const steps = turn.part("steps");
    if (turn.steps.length) {
      turn.part("step-list").lastElementChild?.classList.add("is-done");
      turn.part("steps-summary").textContent = (success ? "Worked through " : "Stopped after ") + turn.steps.length + (turn.steps.length === 1 ? " step" : " steps");
    }
    steps.open = false;
    if (success) {
      turn.status.textContent = "";
      foot.hidden = false;
      turn.part("expand").hidden = turn.depth === "research";
      const meta = turn.part("meta");
      meta.textContent = metaText(data);
      if (data.input_tokens != null) meta.title = (data.model_calls || 1) + " model calls · " + (data.input_tokens + data.output_tokens).toLocaleString() + " tokens";
      thread.querySelectorAll("[data-turn-followups]").forEach(n => n.replaceChildren());
      const list = turn.part("followups");
      if (data.followups?.length) list.append(el("p", { class: "ai-block-label", text: "Ask next" }));
      for (const question of data.followups || []) {
        const button = el("button", { type: "button", class: "ai-followup-chip" }, el("span", { text: question }), el("span", { class: "ai-followup-arrow", "aria-hidden": "true", text: isFull() ? "→" : "↗" }));
        button.addEventListener("click", () => start(isFull() ? find("[data-ai-depth]").value : "research", { parent: turn.runId, question }));
        list.append(button);
      }
      try { sessionStorage.setItem("archer-ai:" + root.dataset.query, runId); } catch {}
      setFooter(turn.depth === "research" ? "AI Mode · " + (data.model_calls || 1) + " model calls" : "Balanced · 1 AI answer");
    } else {
      turn.status.textContent = data.stopped ? "Stopped" : "Couldn't finish";
      turn.part("error-text").textContent = data.text || "The answer was interrupted.";
      turn.part("error").hidden = false;
      if (turn.blocks.length) {
        foot.hidden = false;
        turn.part("expand").hidden = true;
        turn.part("meta").textContent = "Incomplete answer · check the sources";
      }
    }
    updatePrompt();
  };
  const consume = (event, data) => {
    if (event === "run") { runId = data.id; turn.runId = data.id; turn.depth = data.depth; turn.node.dataset.depth = data.depth; }
    else if (event === "status") { root.dataset.stage = data.stage; turn.status.textContent = data.text; step(data.text, data.stage); }
    else if (event === "sources") renderSources(data.sources);
    else if (event === "notice") notice(data.text);
    else if (event === "card") renderCard(data);
    else if (event === "block") renderBlock(data);
    else if (event === "complete" || event === "error") finish(event === "complete", data);
  };

  async function stop(silent = false) {
    version += 1;
    if (busy && runId) request("runs/" + runId + "/cancel", {}, { keepalive: true }).catch(() => {});
    controller?.abort();
    controller = null;
    if (busy && !silent) finish(false, { stopped: true, text: "Stopped. Anything shown above is incomplete." });
    else if (busy) setBusy(false);
  }
  async function start(target, extra = {}) {
    if (target === "research" && !isFull()) enterFullView();
    if (!configured || !consent) {
      pending = { target, extra };
      mode(target === "research" ? "research" : "balanced");
      openSettings(configured ? "" : "Add a key in Settings, then reload this page.");
      return;
    }
    await stop(true);
    const followupTurn = Boolean(extra.question || extra.selected);
    if (extra.replace || (!followupTurn && extra.parent)) turn?.node.remove();
    else if (!extra.parent) turns().forEach(n => n.remove());
    else if (turn && !turn.blocks.length) turn.node.remove();
    const question = extra.question || root.dataset.query;
    if (!root.dataset.query && extra.question) {
      root.dataset.query = extra.question;
      history.replaceState({ archerAI: true }, "", "/ai?q=" + encodeURIComponent(extra.question));
    }
    const body = { snapshot: root.dataset.snapshot, depth: target };
    for (const key of ["automatic", "parent", "question", "selected", "regenerate"]) if (extra[key]) body[key] = extra[key];
    const { replace, regenerate, automatic, ...kept } = extra;
    turn = newTurn(target, question, kept);
    const current = ++version;
    runId = "";
    mode(target === "research" || isFull() ? "research" : "balanced");
    root.dataset.stage = "gathering";
    workspace.hidden = false;
    followup.hidden = !isFull();
    prompt.hidden = true;
    settingsPanel.hidden = true;
    setBusy(true);
    turn.status.textContent = "Gathering the evidence";
    if (isFull() && turns().length > 1) turn.node.scrollIntoView({ block: "start", behavior: "smooth" });
    controller = new AbortController();
    setFooter(target === "research" ? "AI Mode · researching" : "Balanced · answering");
    try {
      const response = await request("runs", body, { signal: controller.signal });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", terminal = false;
      while (true) {
        const { value, done } = await reader.read();
        if (current !== version) { await reader.cancel(); return; }
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n"); buffer = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const { event, data } = JSON.parse(line);
          consume(event, data);
          if (event === "complete" || event === "error") terminal = true;
        }
        if (done) break;
      }
      if (!terminal) finish(false);
    } catch (error) {
      if (current === version && error.name !== "AbortError") finish(false, { text: error.message });
    }
  }

  // ------------------------------------------------------------ wiring
  find("[data-ai-ask]").addEventListener("click", () => {
    if (answered() && turns().every(n => n.dataset.depth !== "research")) { workspace.hidden = false; mode("balanced"); updatePrompt(); }
    else start("compact");
  });
  find("[data-ai-research]").addEventListener("click", () => {
    if (turns().some(n => n.dataset.depth === "research")) enterFullView();
    else start("research");
  });
  modes.forEach(button => button.addEventListener("click", async () => {
    const value = button.dataset.aiMode;
    if (value === "classic") {
      if (isFull()) leaveFullView();
      await stop();
      workspace.hidden = true;
      mode("classic");
      setFooter("Classic mode · no AI used");
      // An explicit Classic choice also disables automatic Balanced on the next search.
      if (defaultMode === "balanced") {
        try { await request("preferences", { consent, mode: "classic" }); defaultMode = "classic"; const box = find("[data-ai-balanced]"); if (box) box.checked = false; }
        catch (error) { openSettings(error.message); }
      }
      updatePrompt();
    } else if (value === "balanced") {
      const compact = turns().length && turns().every(n => n.dataset.depth === "compact");
      if (compact && (busy || answered())) { workspace.hidden = false; mode("balanced"); updatePrompt(); }
      else start("compact");
    } else if (turns().some(n => n.dataset.depth === "research") || isFull()) enterFullView();
    else start("research");
  }));

  const save = find("[data-ai-save]");
  save?.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const response = await request("preferences", { consent: find("[data-ai-consent]").checked, mode: find("[data-ai-balanced]").checked ? "balanced" : "classic" });
      const data = await response.json();
      consent = data.consent; defaultMode = data.default_mode; csrf = data.csrf;
      if (!consent) {
        await stop(true);
        pending = null;
        if (isFull()) leaveFullView();
        workspace.hidden = true;
        mode("classic");
        settingsStatus.textContent = "Cloud AI is off.";
        return;
      }
      if (pending) { const action = pending; pending = null; settingsPanel.hidden = true; start(action.target, action.extra); }
      else { settingsStatus.textContent = "Saved."; setTimeout(closeSettings, 700); }
    } catch (error) { settingsStatus.textContent = error.message; }
    finally { save.disabled = false; }
  });
  find("[data-ai-settings-cancel]")?.addEventListener("click", closeSettings);
  find("[data-ai-settings-close]").addEventListener("click", closeSettings);
  find("[data-ai-rail-settings]").addEventListener("click", () => settingsPanel.hidden ? openSettings() : closeSettings());

  selectButton.addEventListener("click", () => {
    sourcesPanel.open = true;
    setSelecting(!sourcesPanel.classList.contains("is-selecting"));
  });
  sources.addEventListener("change", updateCompare);
  // In AI Mode the rail is always expanded; only the narrow-screen overlay closes.
  sourcesPanel.addEventListener("toggle", () => { if (isFull() && !sourcesPanel.open) sourcesPanel.open = true; });
  compareButton.addEventListener("click", () => {
    const selected = [...sources.querySelectorAll("input:checked")].map(n => n.value);
    if (selected.length < 2 || selected.length > 4) return;
    setSelecting(false);
    document.body.classList.remove("ai-sources-open");
    start("research", { selected, ...(turn?.runId && turn.node.dataset.state === "complete" ? { parent: turn.runId } : {}) });
  });
  find("[data-ai-sources-close]").addEventListener("click", () => {
    if (isFull()) document.body.classList.remove("ai-sources-open");
    else sourcesPanel.open = false;
  });
  find("[data-ai-toggle-sources]").addEventListener("click", () => {
    sourcesPanel.open = true;
    document.body.classList.toggle("ai-sources-open");
  });
  find("[data-ai-composer-sources]").addEventListener("click", () => {
    sourcesPanel.open = true;
    document.body.classList.add("ai-sources-open");
    sourcesPanel.querySelector("summary").focus();
  });

  followup.addEventListener("submit", event => {
    event.preventDefault();
    if (busy) { stop(); return; }
    const question = composerInput.value.trim();
    if (!question) return;
    const parent = turn?.node.dataset.state === "complete" ? turn.runId : "";
    composerInput.value = "";
    composerInput.style.height = "";
    start(find("[data-ai-depth]").value, { ...(parent ? { parent } : {}), question });
  });
  composerInput.addEventListener("input", () => {
    composerInput.style.height = "auto";
    composerInput.style.height = Math.min(200, composerInput.scrollHeight) + "px";
    syncComposer();
  });
  composerInput.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!busy && composerInput.value.trim()) followup.requestSubmit();
    }
  });
  find("[data-ai-begin]").addEventListener("click", () => start("research"));
  find("[data-ai-back]").addEventListener("click", leaveFullView);
  find("[data-ai-web]").addEventListener("click", leaveFullView);
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (!settingsPanel.hidden) closeSettings();
    else if (document.body.classList.contains("ai-sources-open")) document.body.classList.remove("ai-sources-open");
    else if (sourcesPanel.classList.contains("is-selecting")) setSelecting(false);
  });
  window.addEventListener("popstate", () => {
    if (location.pathname === "/ai") enterFullView(false);
    else leaveFullView();
  });
  window.addEventListener("pagehide", () => stop(true));

  // ------------------------------------------------------------ first paint
  const restore = async () => {
    let saved;
    try { saved = sessionStorage.getItem("archer-ai:" + root.dataset.query); } catch {}
    if (!saved) return;
    try {
      const response = await fetch("/api/ai/runs/" + encodeURIComponent(saved), { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      root.dataset.snapshot = data.snapshot;
      const chain = data.turns || [{ question: data.question, events: data.events }];
      let parent = "";
      chain.forEach((item, index) => {
        const [question, compare] = item.question.split("\nCompare the selected sources: ");
        const next = chain[index + 1];
        // "Go deeper" re-asks the same question; only the deeper answer is kept on screen.
        const superseded = next && next.question === item.question;
        const extra = { ...(parent ? { parent } : {}), ...(index && question !== root.dataset.query ? { question } : {}), ...(compare ? { selected: compare.split(", ") } : {}) };
        const runEvent = item.events.find(e => e.event === "run");
        if (!superseded) {
          turn = newTurn(item.depth || "research", question, extra);
          for (const entry of item.events) consume(entry.event, entry.data);
          turn.part("meta").append(document.createTextNode(" · restored, no new AI call"));
        }
        parent = runEvent ? runEvent.data.id : parent;
      });
      mode("research");
    } catch { /* An expired cached answer never triggers paid regeneration. */ }
  };
  if (root.dataset.fullview === "true") {
    enterFullView(false);
    restore();
  } else if (defaultMode === "balanced" && root.dataset.eligible === "true" && consent && configured) start("compact", { automatic: true });
  else updatePrompt();
});

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
const box = $(".searchbar input[name='q']");
for (const [index, wrapper] of $$("[data-searchbox]").entries()) {
  const form = $("form", wrapper), input = $("input[name='q']", form);
  const list = el("div", { id: `suggestions-${index}`, class: "suggestions", role: "listbox", "aria-label": "Search suggestions" });
  const status = el("span", { class: "sr-only", role: "status", "aria-live": "polite" });
  list.hidden = true;
  wrapper.append(list, status);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");
  let timer, controller, generation = 0, composing = false, pointerSelecting = false;
  let touchStart = null;
  let terms = [], active = -1, draft = input.value;
  const eligible = (q) => q.length <= Number(wrapper.dataset.suggestMax) &&
    (q.trim().length >= Number(wrapper.dataset.suggestMin) || /^[!:]/.test(q.trim()));
  const close = (restore = false) => {
    if (restore && active >= 0) input.value = draft;
    clearTimeout(timer);
    controller?.abort();
    generation++;
    terms = []; active = -1;
    list.hidden = true;
    list.replaceChildren();
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    status.textContent = "";
  };
  const choose = (value) => {
    input.value = value;
    close();
    form.requestSubmit();
  };
  const select = (next) => {
    active = next;
    input.value = active < 0 ? draft : terms[active];
    [...list.children].forEach((row, i) => row.setAttribute("aria-selected", String(i === active)));
    if (active < 0) input.removeAttribute("aria-activedescendant");
    else {
      input.setAttribute("aria-activedescendant", list.children[active].id);
      list.children[active].scrollIntoView({ block: "nearest" });
    }
  };
  const schedule = () => {
    close();
    draft = input.value;
    if (composing || !eligible(draft) || document.activeElement !== input) return;
    const requested = draft, ticket = generation;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`/suggest?q=${encodeURIComponent(requested)}`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json();
        if (ticket !== generation || composing || document.activeElement !== input || input.value !== requested) return;
        if (!Array.isArray(data) || data[0] !== requested || !Array.isArray(data[1])) return;
        terms = [...new Set(data[1].filter((s) => typeof s === "string" && s.trim() && s.length <= Number(wrapper.dataset.suggestMax)))].slice(0, 8);
        if (!terms.length) return;
        list.replaceChildren(...terms.map((term, i) => {
          const row = el("div", { id: `${list.id}-${i}`, class: "suggestion", role: "option", "aria-selected": "false" });
          const label = el("span", { class: "suggestion-text" });
          const match = term.toLocaleLowerCase().indexOf(requested.trim().toLocaleLowerCase());
          if (match < 0) label.textContent = term;
          else label.append(document.createTextNode(term.slice(0, match)),
            el("strong", { text: term.slice(match, match + requested.trim().length) }),
            document.createTextNode(term.slice(match + requested.trim().length)));
          const searchIcon = icon("search");
          searchIcon.setAttribute("aria-hidden", "true");
          $("use", searchIcon).setAttribute("href", "#i-search");
          row.append(searchIcon, label);
          row.addEventListener("click", () => { if (!list.hidden) choose(term); });
          return row;
        }));
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        status.textContent = `${terms.length} search suggestions available. Use up and down arrows to choose.`;
      } catch (_) { /* Offline or superseded: the plain search form still works. */ }
    }, 180);
  };
  input.addEventListener("input", schedule);
  input.addEventListener("focus", schedule);
  input.addEventListener("compositionstart", () => { composing = true; close(true); });
  input.addEventListener("compositionend", () => { composing = false; schedule(); });
  input.addEventListener("blur", () => { if (!pointerSelecting) close(true); });
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || composing) return;
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); close(true);
    } else if (e.key === "Tab") close(true);
    else if (e.key === "Enter" && active >= 0 && !list.hidden) {
      e.preventDefault(); choose(terms[active]);
    } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !list.hidden) {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (active + 1 >= terms.length ? -1 : active + 1) : (active < 0 ? terms.length - 1 : active - 1);
      select(next);
    }
  });
  list.addEventListener("pointerdown", (e) => {
    pointerSelecting = true;
    if (e.pointerType === "mouse") e.preventDefault();
    else {
      const row = e.target.closest(".suggestion");
      touchStart = row ? { x: e.clientX, y: e.clientY, value: terms[[...list.children].indexOf(row)], moved: false } : null;
    }
  });
  list.addEventListener("pointermove", (e) => {
    if (touchStart && Math.hypot(e.clientX - touchStart.x, e.clientY - touchStart.y) > 10) touchStart.moved = true;
  });
  const releasePointer = (e) => {
    if (!pointerSelecting) return;
    pointerSelecting = false;
    // Touch's synthesized click follows a blur. Commit a stationary tap on release
    // while leaving native scrolling and pointercancel untouched.
    if (e.type === "pointerup" && touchStart && !touchStart.moved && !list.hidden &&
        Math.hypot(e.clientX - touchStart.x, e.clientY - touchStart.y) <= 10) choose(touchStart.value);
    touchStart = null;
    setTimeout(() => { if (document.activeElement !== input) close(true); }, 0);
  };
  addEventListener("pointerup", releasePointer);
  addEventListener("pointercancel", releasePointer);
  form.addEventListener("submit", () => close());
  const clear = $(".searchbar-clear", form);
  const updateClear = () => { clear.hidden = !input.value; };
  input.addEventListener("input", updateClear);
  clear.addEventListener("click", () => { close(); input.value = ""; draft = ""; updateClear(); input.focus(); });
  addEventListener("pageshow", () => { close(); updateClear(); });
  updateClear();
  // Home's autofocus can occur before this deferred script installs listeners.
  if (document.activeElement === input) schedule();
}

// While the next page loads (new search or a tab switch), the pixel mark speeds up and a dot
// scanner runs along the bottom of the header.
const headerBar = $(".top");
if (headerBar) {
  const busy = () => headerBar.classList.add("is-busy");
  $(".top .searchbar")?.addEventListener("submit", busy);
  $$(".top .tab").forEach((a) => { if (a.host === location.host) a.addEventListener("click", busy); });
  addEventListener("pageshow", () => headerBar.classList.remove("is-busy"));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && box && document.activeElement !== box && !/input|textarea|select/i.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
    e.preventDefault(); box.focus(); box.select();
  }
});

// The header has exactly two states: expanded or collapsed (search row slid away, result types
// pinned). The slide itself is a timed CSS transition, never tied to the scroll position.
// The direction comes from what the user does (wheel, touch, keys, dragging the scrollbar), not from
// scrollY changes: lazy images and strips resize the page, and the browser then shifts scrollY by
// itself, which used to read as "scrolling down" and made the header flip back and forth.
const topBar = $(".top");
if (topBar) {
  const TRAVEL = 40, LOCK = 380;
  let run = 0, lockedUntil = 0, touchY = null, dragging = false, last = scrollY;
  const set = (collapsed) => {
    if (topBar.classList.contains("is-collapsed") === collapsed) return;
    topBar.classList.toggle("is-collapsed", collapsed);
    lockedUntil = performance.now() + LOCK;
    run = 0;
  };
  const intent = (dy) => {
    if (!dy || performance.now() < lockedUntil || topBar.contains(document.activeElement)) return;
    run = Math.sign(dy) === Math.sign(run) ? run + dy : dy;   // distance travelled in the current direction
    if (run > TRAVEL && scrollY > 80) set(true);
    else if (run < -TRAVEL) set(false);
  };
  addEventListener("wheel", (e) => intent(e.ctrlKey ? 0 : e.deltaY * (e.deltaMode === 1 ? 16 : 1)), { passive: true });
  addEventListener("touchstart", (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  addEventListener("touchmove", (e) => {
    const y = e.touches[0].clientY;
    if (touchY != null) intent(touchY - y);
    touchY = y;
  }, { passive: true });
  addEventListener("keydown", (e) => {
    if (/input|textarea|select/i.test(document.activeElement.tagName)) return;
    const down = { ArrowDown: 1, PageDown: 1, End: 1, " ": e.shiftKey ? -1 : 1 }[e.key];
    const up = { ArrowUp: 1, PageUp: 1, Home: 1 }[e.key];
    if (down) intent(down * 120);
    else if (up) intent(-120);
  });
  addEventListener("pointerdown", (e) => { dragging = e.clientX >= document.documentElement.clientWidth; });
  addEventListener("pointerup", () => { dragging = false; });
  addEventListener("scroll", () => {
    const y = Math.max(scrollY, 0), dy = y - last;
    last = y;
    if (y < 8) set(false);                  // always open at the very top
    else if (dragging) intent(dy);          // scrollbar drag has no wheel events
  }, { passive: true });
  topBar.addEventListener("focusin", () => set(false));
}

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
  const main = $(".collage-main", collage);
  const mainImage = $("img", main);
  const recoverMain = async () => {
    if (mainImage?.dataset.fallback && mainImage.src !== mainImage.dataset.fallback) {
      const fallback = mainImage.dataset.fallback;
      mainImage.dataset.fallback = "";
      const probe = new Image();
      probe.referrerPolicy = "no-referrer";
      if (await new Promise((resolve) => { probe.onload = () => resolve(true); probe.onerror = () => resolve(false); probe.src = fallback; })) {
        mainImage.src = fallback;
        return;
      }
    }
    const items = await strip("images", collage.dataset.collageQ);
    const pic = await firstLoading(items.filter((i) => isHttp(i.thumb) && isHttp(i.url)));
    if (pic) {
      main.replaceWith(el("a", { class: "collage-main", href: pic.url, title: pic.title }, img(pic.thumb, pic.title)));
      collage.classList.remove("collage--empty", "is-logo");
    } else if (mainImage) {
      main.replaceWith(el("div", { class: "collage-main collage-placeholder" }, icon("tv")));
    }
  };
  if (mainImage) {
    mainImage.addEventListener("error", recoverMain, { once: true });
    if (mainImage.complete && !mainImage.naturalWidth) recoverMain();
  } else if (collage.classList.contains("collage--empty")) {
    recoverMain();
  }
  if (collage.closest(".is-poster")) continue;
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
  fetch(`/suggest?q=${encodeURIComponent(q)}`, { cache: "no-store" }).then((r) => r.ok ? r.json() : []).catch(() => []).then((data) => {
    const list = (Array.isArray(data) && Array.isArray(data[1]) ? data[1] : [])
      .filter((s) => typeof s === "string" && s.toLowerCase() !== q.toLowerCase()).slice(0, 8);
    if (!list.length) return;
    const tab = section.dataset.relatedTab;
    $(".chips", section).replaceChildren(...list.map((s) =>
      el("a", { class: "chip", href: `/search?q=${encodeURIComponent(s)}${tab && tab !== "all" ? `&tab=${encodeURIComponent(tab)}` : ""}`, text: s })));
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

// ------------------------------------------- AI model chart: show / hide marks
// Each key row toggles its model's mark; "All other results" toggles the background dots.
// A hidden model leaves the graph entirely, and the remaining labels close up around it:
// the same placement pass as aimodels.place_labels, with measured text widths.
function placeAmLabels(svg) {
  const right = +svg.dataset.right, top = +svg.dataset.top, bottom = +svg.dataset.bottom;
  const marks = $$("[data-mark]", svg).filter((g) => !g.classList.contains("is-off")).map((g) => {
    const dot = $(".am-dot", g);
    return { g, x: +dot.getAttribute("cx"), y: +dot.getAttribute("cy"), self: g.classList.contains("am-mark--self") };
  });
  const dots = marks.map((m) => [m.x - 7, m.x + 7, m.y - 7, m.y + 7]);
  const overlaps = (a, b) => a[0] < b[1] && b[0] < a[1] && a[2] < b[3] && b[2] < a[3];
  const placed = [];
  marks.sort((a, b) => (a.self === b.self ? a.y - b.y : a.self ? -1 : 1));
  for (const m of marks) {
    const label = $(".am-lbl", m.g), leader = $(".am-leader", m.g);
    const width = label.getComputedTextLength() + 2;
    const start = m.x + 9 + width <= right;
    const lx = start ? m.x + 9 : m.x - 9;
    const [x0, x1] = start ? [lx, lx + width] : [lx - width, lx];
    const others = [...placed, ...dots.filter((d) => d[0] !== m.x - 7 || d[2] !== m.y - 7)];
    let ly = null;
    for (let step = 0; step <= 16 && ly === null; step++) {
      const y = m.y + 4 + (step ? 13 * Math.ceil(step / 2) * (step % 2 ? 1 : -1) : 0);
      if (y < top + 10 || y > bottom - 2) continue;
      if (!others.some((o) => overlaps([x0, x1, y - 10, y + 3], o))) ly = y;
    }
    if (ly === null) ly = Math.min(Math.max(m.y + 4, top + 10), bottom - 2);
    placed.push([x0, x1, ly - 10, ly + 3]);
    label.setAttribute("x", lx); label.setAttribute("y", ly);
    label.setAttribute("text-anchor", start ? "start" : "end");
    leader.setAttribute("x2", start ? lx - 3 : lx + 3); leader.setAttribute("y2", ly - 4);
    leader.hidden = Math.abs(ly - 4 - m.y) <= 6;
  }
}
// The best-value line joins only what is visible, so a hidden model leaves no bend behind.
function drawAmFrontier(svg) {
  const line = $(".am-frontier", svg);
  if (!line) return;
  const dots = $("[data-am-dots]", svg);
  const circles = [...$$("[data-mark]:not(.is-off) .am-dot", svg), ...(dots.classList.contains("is-off") ? [] : $$("circle", dots))];
  // SVG y grows downward: walking right (pricier), keep each point that scores higher (smaller y).
  const pts = circles.map((c) => [+c.getAttribute("cx"), +c.getAttribute("cy")]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let best = Infinity;
  const kept = pts.filter(([, y]) => (y < best ? ((best = y), true) : false));
  line.setAttribute("points", kept.map((p) => p.join(",")).join(" "));
  line.style.display = kept.length > 1 ? "" : "none";
}
for (const button of $$("[data-am-toggle]")) {
  button.addEventListener("click", () => {
    const panel = button.closest("[role=tabpanel]");
    const key = button.dataset.amToggle;
    const target = key === "dots" ? $("[data-am-dots]", panel) : $(`[data-mark="${key}"]`, panel);
    const on = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(on));
    if (!target) return;
    target.classList.toggle("is-off", !on);
    const svg = $("[data-am-plot]", panel);
    if (key !== "dots") placeAmLabels(svg);
    drawAmFrontier(svg);
  });
}

// ------------------------------------------------------- AI model chart tabs
for (const chart of $$("[data-am-tabs]")) {
  const tabs = $$("[role=tab]", chart);
  const select = (tab) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(t.getAttribute("aria-controls"));
      panel.hidden = !on;
      if (on) placeAmLabels($("[data-am-plot]", panel));
    }
  };
  const first = $(".am-panel:not([hidden]) [data-am-plot]", chart);
  if (first) placeAmLabels(first);
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      const next = tabs[(i + step + tabs.length) % tabs.length];
      select(next); next.focus(); e.preventDefault();
    });
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

// ------------------------------------------------- music players (songs/albums)
// Nothing third-party loads until the user presses play.
const PLAYER_HOSTS = ["https://www.youtube-nocookie.com/", "https://open.spotify.com/", "https://embed.music.apple.com/"];
function mountEmbed(pane) {
  if (pane.querySelector("iframe")) return;
  const src = pane.dataset.embed;
  if (!PLAYER_HOSTS.some((h) => src.startsWith(h))) return;
  const frame = el("iframe", {
    src, title: `${pane.dataset.kind} player`, loading: "lazy",
    allow: "autoplay; encrypted-media; fullscreen; picture-in-picture; clipboard-write",
    allowfullscreen: "",
    // YouTube refuses embeds without a Referer; the page itself sends none.
    referrerpolicy: "strict-origin-when-cross-origin",
  });
  pane.replaceChildren(frame);
}
for (const pane of $$(".player-pane, .music-embed")) {
  $(".player-facade, .music-embed-load", pane)?.addEventListener("click", () => mountEmbed(pane));
  if (pane.dataset.kind === "spotify") mountEmbed(pane);
}

for (const panel of $$("[data-lyrics-title]")) {
  const status = $(".music-lyrics-status", panel);
  const source = $(".music-lyrics-source", panel);
  getJSON(`/api/lyrics?title=${encodeURIComponent(panel.dataset.lyricsTitle)}&artist=${encodeURIComponent(panel.dataset.lyricsArtist || "")}`)
    .then((data) => {
      if (!data || !data.text) { status.textContent = "No matching lyrics found yet."; return; }
      status.replaceWith(el("div", { class: "music-lyrics-text", text: data.text }));
      if (isHttp(data.url)) {
        source.href = data.url;
        source.firstChild.textContent = "Lyrics from LRCLIB ";
      }
    });
}

// ------------------------------------------------- where to watch (live, TMDB)
for (const panel of $$("[data-watch-tmdb]")) {
  const groups = $("[data-watch-groups]", panel), fallback = $("[data-watch-fallback]", panel);
  const showFallback = (note) => {
    groups.remove(); fallback.hidden = false;
    panel.closest(".hero-entity")?.classList.toggle("watch-compact", $$(".watch-service", fallback).length <= 1);
    if (note) fallback.prepend(el("p", { class: "watch-note", text: note }));
  };
  getJSON(`/api/watch?media=${encodeURIComponent(panel.dataset.watchMedia)}&tmdb=${encodeURIComponent(panel.dataset.watchTmdb)}`)
    .then((data) => {
      if (!data || data.error || !Array.isArray(data.groups)) return showFallback("Live availability couldn't load.");
      if (!data.groups.length) return showFallback("Not streaming, rentable or for sale here right now.");
      const link = isHttp(data.link) ? data.link : null;
      const count = new Set(data.groups.flatMap((g) => g.providers.map((p) => p.name))).size;
      panel.closest(".hero-entity")?.classList.toggle("watch-compact", count === 1);
      groups.replaceChildren(...data.groups.map((g) => el("div", { class: "watch-group" + (g.free ? " is-free" : "") },
        el("p", { class: "watch-group-label", text: g.label }),
        el("div", { class: "watch-logos" }, ...g.providers.map((p) => {
          const logo = isHttp(p.logo) ? el("img", { src: p.logo, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : el("span", { class: "watch-provider-fallback", text: p.name.slice(0, 1) });
          return el(link ? "a" : "span", { class: "watch-provider", href: link, title: p.name }, logo, el("span", { text: p.name }), link ? el("span", { class: "watch-provider-arrow", text: "↗" }) : null);
        })))));
    });
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

// ------------------------------------------------------ dot-matrix fields
// A grid of rounded squares that is densest at the bottom and thins out and fades toward the top.
// The horizon curves: the fade starts lower in the middle and climbs higher at the edges. Each dot
// steps between a few ink levels at random intervals. Used on the home page and at the foot of
// the results page; it only animates while on screen.
function dotField(canvas, { pitch = 12, sq = 9, density = 0.7, curve = 0.45, ink = 1 } = {}) {
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const LEVELS = [0.05, 0.12, 0.24, 0.42, 0.7];
  let cols, fade, level, next, sprite, dpr, visible = true, last = 0;

  const layout = () => {
    dpr = devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    cols = Math.ceil(w / pitch);
    const rows = Math.ceil(h / pitch), now = performance.now();
    fade = new Float32Array(cols * rows); level = new Float32Array(cols * rows); next = new Float64Array(cols * rows);
    for (let c = 0; c < cols; c++) {
      const u = ((c + 0.5) / cols) * 2 - 1;             // -1 at the left edge, 1 at the right
      const horizon = curve * (1 - u * u);              // how far down the field starts in this column
      for (let r = 0; r < rows; r++) {
        const depth = ((r + 1) / rows - horizon) / (1 - horizon);   // 0 at the horizon, 1 at the bottom
        if (depth <= 0) continue;
        const k = r * cols + c;
        // Fewer dots survive higher up, and the ones that do are fainter.
        fade[k] = Math.random() < density * depth ** 1.1 ? ink * depth ** 1.8 : 0;
        level[k] = LEVELS[(Math.random() * LEVELS.length) | 0];
        next[k] = now + Math.random() * 3000;
      }
    }
    sprite = document.createElement("canvas");
    sprite.width = sprite.height = Math.ceil(sq * dpr);
    const g = sprite.getContext("2d");
    g.scale(dpr, dpr); g.fillStyle = "#ededed";
    g.beginPath(); g.roundRect(0, 0, sq, sq, sq * 0.25); g.fill();
  };

  const draw = (now) => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const off = (pitch - sq) / 2, size = sprite.width;
    for (let k = 0; k < fade.length; k++) {
      if (!fade[k]) continue;
      if (!still && now >= next[k]) {
        // Mostly dim, sometimes a bright flash that holds only briefly.
        const i = Math.random() < 0.08 ? LEVELS.length - 1 : (Math.random() * (LEVELS.length - 1)) | 0;
        level[k] = LEVELS[i];
        next[k] = now + (i === LEVELS.length - 1 ? 180 + Math.random() * 400 : 600 + Math.random() * 2800);
      }
      ctx.globalAlpha = level[k] * fade[k];
      ctx.drawImage(sprite, Math.round(((k % cols) * pitch + off) * dpr), Math.round(((k / cols | 0) * pitch + off) * dpr), size, size);
    }
  };

  const tick = (now) => {
    if (visible && now - last > 66) { draw(now); last = now; }   // ~15fps: the motion is stepped anyway
    requestAnimationFrame(tick);
  };
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; }).observe(canvas);
  layout();
  if (still) draw(performance.now()); else requestAnimationFrame(tick);
  addEventListener("resize", () => { layout(); if (still) draw(performance.now()); });
}

const homeField = $(".home-field");
if (homeField) dotField(homeField);
const serpField = $(".serp-field");
if (serpField) dotField(serpField, { pitch: 10, sq: 7.5, density: 0.55, curve: 0.55, ink: 0.8 });
