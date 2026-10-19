// Archer results page: lazy modules and small interactive widgets.
// Everything is built with DOM APIs + textContent; no HTML from the network is injected.
"use strict";

// A knowledge panel that arrived after the results (templates/search_late.html) moves into the
// place the results left for it, before any other code here looks at the page.
for (const late of document.querySelectorAll("[data-late]")) {
  const slot = document.querySelector(`[data-late-slot="${late.dataset.late}"]`);
  if (slot) slot.replaceWith(...late.childNodes);
  late.remove();
}
document.querySelectorAll("[data-late-slot]").forEach(slot => slot.remove());  // nothing came for these

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
    if (!isFull()) sourcesPanel.open = false;  // expanding shows the answer, not the source list
    try { localStorage.setItem("archer-ai-small", value ? "1" : "0"); } catch {}
    applySize();
  };
  // Pictures come only when the server judged them useful (visuals.py), already filtered for relevance.
  // A quick answer shows them as a thin row; research shows a titled gallery card.
  const visualRow = (t, data) => {
    const row = t.part("visuals");
    const tiles = (data.items || []).map(item => {
      const picture = img(item.thumbnail, item.title);
      if (!picture || !isHttp(item.url)) return null;
      const tile = el("a", { class: "ai-visual", href: item.url, target: "_blank", rel: "noopener noreferrer", title: item.title + (item.host ? " · " + item.host : ""), "data-ref": item.ref || "" }, picture);
      picture.addEventListener("error", () => tile.remove());
      return tile;
    }).filter(Boolean);
    if (!tiles.length) return;
    row.replaceChildren(...tiles);
    row.hidden = false;
  };
  // The answer model's verdict: keep only pictures of the actual subject, in its order, or none.
  const applyVisuals = (t, data) => {
    const tiles = [...t.node.querySelectorAll("[data-ref]")].filter(n => n.dataset.ref);
    if (!tiles.length) return;
    const keep = data.verdict === "show" ? data.keep || [] : [];
    const containers = new Set(tiles.map(n => n.parentElement));
    for (const tile of tiles) if (!keep.includes(tile.dataset.ref)) tile.remove();
    for (const box of containers) {
      [...box.children].filter(n => n.dataset.ref).sort((a, b) => keep.indexOf(a.dataset.ref) - keep.indexOf(b.dataset.ref)).forEach(n => box.append(n));
      if (box.children.length) continue;
      const gone = box.closest(".ai-tool-card--media") || box;
      gone.classList.add("is-leaving");
      setTimeout(() => { gone.hidden = true; gone.classList.remove("is-leaving"); }, 220);
    }
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
    find("[data-ai-composer-count]").textContent = String(items.length);
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
  // **bold** for defined terms, ==highlight== for the phrase the model thinks should catch the eye.
  const formatted = text => {
    const fragment = document.createDocumentFragment();
    for (const part of text.split(/(\*\*[^*]+\*\*|==[^=\n]+==)/g)) {
      if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) fragment.append(el("strong", { text: part.slice(2, -2) }));
      else if (part.length > 4 && part.startsWith("==") && part.endsWith("==")) fragment.append(el("mark", { class: "ai-mark", text: part.slice(2, -2) }));
      else fragment.append(document.createTextNode(part));
    }
    return fragment;
  };
  const plain = text => (text || "").replace(/==([^=\n]+)==/g, "$1");
  const CALLOUT = { key: ["Key point", "u-sparkle"], tip: ["Tip", "u-sparkle"], warning: ["Watch out", "u-shield"] };
  const renderBlock = block => {
    const tone = block.kind === "callout" ? " ai-block--" + (block.tone || "key") : "";
    const section = el("section", { class: "ai-block ai-block--" + block.kind + tone });
    if (block.kind === "summary") section.setAttribute("aria-label", "Answer summary");
    if (block.kind === "inference") section.append(el("span", { class: "ai-block-label", text: "Archer's take" }));
    if (block.kind === "limitation") section.append(el("span", { class: "ai-block-label", text: "What remains uncertain" }));
    if (block.kind === "callout") section.append(el("span", { class: "ai-block-label", text: (CALLOUT[block.tone] || CALLOUT.key)[0] }));
    if (block.heading) section.append(el("h3", { text: block.heading }));
    if (block.kind === "facts") {
      const tiles = block.items.map(item => el("div", { class: "ai-keyfact", "data-picture": item.picture || null },
        el("div", { class: "ai-keyfact-body" }, el("dt", { text: item.label }), el("dd", {}, formatted(item.text), citations(item.citations)))));
      section.append(el("dl", { class: "ai-keyfacts" }, ...tiles));
      // A fact naming a person, place or work gets its Wikipedia photo (exact article titles only).
      const pictured = tiles.filter(t => t.dataset.picture);
      if (pictured.length) {
        getJSON("/api/portraits?" + pictured.map(t => "t=" + encodeURIComponent(t.dataset.picture)).join("&")).then(found => {
          for (const tile of pictured) {
            const photo = img(found[tile.dataset.picture], "");
            if (!photo) continue;
            photo.addEventListener("error", () => tile.classList.remove("has-picture"));
            tile.prepend(el("span", { class: "ai-keyfact-picture" }, photo));
            tile.classList.add("has-picture");
          }
        }).catch(() => {});
      }
    } else if (block.kind === "specs") {
      // A product's spec sheet: label + value rows, each with a small icon for the kind of spec.
      section.append(el("dl", { class: "ai-specs" }, ...block.items.map(item => el("div", { class: "ai-spec" },
        el("dt", {}, icon(specIcon(item.label)), document.createTextNode(item.label)),
        el("dd", {}, formatted(item.text), citations(item.citations))))));
    } else if (block.kind === "verdict") {
      const mark = { positive: "✓", mixed: "≈", negative: "✕" }[block.tone] || "≈";
      section.classList.add("ai-verdict--" + block.tone);
      section.prepend(el("span", { class: "ai-verdict-mark", "aria-hidden": "true", text: mark }));
      section.querySelector("h3")?.classList.add("ai-verdict-title");
      if (block.text) section.append(el("p", {}, formatted(block.text), citations(block.citations)));
    } else if (block.kind === "proscons") {
      const side = (items, cls, title, sign) => items.length ? el("div", { class: "ai-pc " + cls },
        el("span", { class: "ai-block-label", text: title }),
        el("ul", {}, ...items.map(item => el("li", { "data-sign": sign }, formatted(item.text), citations(item.citations))))) : null;
      section.append(el("div", { class: "ai-pc-grid" }, side(block.pros, "ai-pc--pro", "Pros", "+"), side(block.cons, "ai-pc--con", "Cons", "−")));
    } else if (block.kind === "products") {
      const row = el("div", { class: "ai-products" });
      for (const item of block.items) {
        const card = isHttp(item.url) ? el("a", { class: "ai-product", href: item.url, target: "_blank", rel: "noopener noreferrer" }) : el("div", { class: "ai-product" });
        const host = hostOf(item.url);
        const photo = img(item.image, item.label);
        const frame = el("span", { class: "ai-product-photo" }, photo || (host ? siteBadge(host) : null));
        if (photo) photo.addEventListener("error", () => { if (host) frame.append(siteBadge(host)); });
        card.append(frame, el("span", { class: "ai-product-body" },
          el("strong", { class: "ai-product-name", text: item.label }),
          item.value ? el("span", { class: "ai-product-price", text: item.value }) : null,
          item.text ? el("span", { class: "ai-product-note" }, formatted(item.text)) : null,
          host ? el("small", { class: "ai-product-host", text: host.replace(/^www\./, "") }) : null));
        row.append(el("div", { class: "ai-product-cell" }, card, citations(item.citations)));
      }
      section.append(row);
    } else if (block.kind === "bars") {
      const values = block.items.map(item => Number(String(item.value).replace(/,/g, "")) || 0);
      const top = Math.max(...values, 1);
      section.append(el("div", { class: "ai-bars", role: "list" }, ...block.items.map((item, i) => {
        const fill = el("span", { class: "ai-bar-fill" });
        fill.style.width = Math.max(2, values[i] / top * 100) + "%";
        return el("div", { class: "ai-bar", role: "listitem" },
          el("span", { class: "ai-bar-label", text: item.label }),
          el("span", { class: "ai-bar-track" }, fill),
          el("span", { class: "ai-bar-value" }, document.createTextNode(Number(values[i]).toLocaleString() + (item.text ? " " : "")), item.text ? formatted(item.text) : "", citations(item.citations)));
      })));
    } else if (block.kind === "timeline") {
      section.append(el("ol", { class: "ai-timeline" }, ...block.items.map(item => el("li", {},
        el("span", { class: "ai-timeline-when", text: item.label }),
        el("span", { class: "ai-timeline-what" }, formatted(item.text), citations(item.citations))))));
    } else if (block.kind === "quote") {
      const source = sourceMap.get((block.citations || [])[0]);
      const who = source?.url ? siteName(hostOf(source.url)) : "Source";
      section.append(el("blockquote", {}, el("p", {}, formatted(block.text.replace(/^["“]|["”]$/g, "")))),
        el("p", { class: "ai-quote-by" }, document.createTextNode("— " + who), citations(block.citations)));
    } else if (block.kind === "table") {
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
    turn.node.classList.toggle("has-more", turn.blocks.length > 1 || turn.cards.childElementCount > 0);
    decodeReveal(section);
  };
  const fact = (label, value) => el("div", { class: "ai-fact" }, el("small", { text: label }), el("strong", { text: String(value) }));
  // The product this research is about: the same listings and scores as the results-page panel.
  const productCard = (panel, data) => {
    panel.classList.add("ai-pcard");
    const lead = [data.brand, data.category].filter(Boolean).join(" · ");
    const price = data.price;
    const body = el("div", { class: "ai-pcard-body" },
      lead ? el("small", { class: "ai-pcard-lead", text: lead }) : null,
      el("strong", { class: "ai-pcard-name", text: data.name }),
      price ? el("p", { class: "ai-pcard-price" }, el("b", { text: price.low_text }), price.high_text ? document.createTextNode(" – " + price.high_text) : null,
        el("span", { text: price.listed ? " · listed price" : price.stores > 1 ? " · " + price.stores + " stores" : " · 1 store" })) : null);
    const stats = el("div", { class: "ai-pcard-stats" });
    if (data.score) stats.append(el("span", {}, scoreRing(data.score.value, true), el("small", { text: "Experts · " + data.score.count + (data.score.count === 1 ? " review" : " reviews") })));
    if (data.rating) {
      const stars = el("span", { class: "pd-stars", "aria-hidden": "true" });
      stars.style.setProperty("--v", data.rating.value);
      stats.append(el("span", {}, stars, el("small", { text: data.rating.value + " / 5 in stores" })));
    }
    if (stats.children.length) body.append(stats);
    // The article photo, else the image search for this model (as on the results page), else a listing thumbnail.
    const photo = el("span", { class: "ai-pcard-photo" }, icon("package"));
    const fill = list => firstLoading(list.filter(isHttp).map(src => ({ thumb: src }))).then(found => { if (found) photo.replaceChildren(img(found.thumb, data.name)); });
    if (data.images?.length) fill([...data.images]);
    else getJSON("/api/product/photos?name=" + encodeURIComponent(data.name) + "&brand=" + encodeURIComponent(data.brand || ""))
      .then(items => fill([...(Array.isArray(items) ? items : []).slice(0, 4).map(i => i.full || i.thumb), ...(data.thumbs || [])]));
    const head = el("div", { class: "ai-pcard-head" }, photo, body);
    const offers = el("div", { class: "ai-pcard-offers" });
    for (const offer of (data.offers || []).filter(o => o.role !== "guide" && isHttp(o.url)).slice(0, 4)) {
      offers.append(el("a", { class: "ai-pcard-offer" + (offer.best ? " is-best" : ""), href: offer.url, target: "_blank", rel: "noopener noreferrer" },
        siteBadge(hostOf(offer.url)), el("span", { text: offer.store }), el("b", { text: offer.price_text || "See price" })));
    }
    panel.append(head);
    if (offers.children.length) panel.append(offers);
  };
  const renderCard = card => {
    if (turn.shown.has(card.source_id)) return;
    turn.shown.add(card.source_id);
    const data = card.data || {};
    const panel = el("article", { class: "ai-tool-card ai-tool-card--" + card.kind },
      el("p", { class: "ai-tool-label", text: card.kind.replace("_", " ") }), el("h3", { text: card.title }));
    if (card.kind === "media" && turn.depth !== "research" && data.category === "images") {
      turn.node.classList.add("has-media");
      visualRow(turn, data);
      return;
    }
    if (card.kind === "media") {
      turn.node.classList.add("has-media");
      const gallery = el("div", { class: "ai-media-grid" + (data.category === "videos" ? " ai-media-grid--videos" : "") });
      for (const item of data.items || []) {
        const tile = safeLink(item.url, "");
        tile.classList.add("ai-media-tile");
        if (item.ref) tile.dataset.ref = item.ref;
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
    } else if (card.kind === "product") {
      productCard(panel, data);
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
    turn.node.classList.add("has-more");
    decodeReveal(panel);
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
    // Balanced's follow-up line: continues in AI Mode, with this answer as the conversation so far.
    const ask = part("ask");
    const askInput = ask.elements.question;
    askInput.addEventListener("input", () => { ask.querySelector("button").disabled = !askInput.value.trim(); });
    ask.addEventListener("submit", event => {
      event.preventDefault();
      const question = askInput.value.trim();
      if (!question || busy) return;
      askInput.value = "";
      start("research", { ...(t.runId && t.node.dataset.state === "complete" ? { parent: t.runId } : {}), question });
    });
    welcome.hidden = true;
    thread.append(node);
    applySize();
    // Full answer shape while nothing has arrived; a two-line tail while more is still coming.
    t.skeletons = [pixelSkeleton(part("skeleton"), [0.34, 0, 1, 0.97, 0.9, 0.58, 0, 1, 0.74]), pixelSkeleton(part("skeleton-tail"), [0.92, 0.55])];
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
    if (data.saved) parts.push("Saved answer · no new AI call");
    else if (data.cost_usd != null) parts.push((data.cost_is_ceiling ? "≤ $" : "~$") + data.cost_usd.toFixed(data.cost_usd < 0.01 ? 4 : 2));
    return parts.filter(Boolean).join(" · ");
  };
  const copyAnswer = async t => {
    const lines = [];
    for (const block of t.blocks) {
      if (block.heading) lines.push("## " + block.heading);
      if (block.kind === "table") {
        lines.push("| " + block.columns.join(" | ") + " |", "|" + block.columns.map(() => " --- |").join(""));
        for (const row of block.rows) lines.push("| " + row.map(c => plain(c.text)).join(" | ") + " |");
      } else if (["facts", "timeline", "products", "bars", "specs"].includes(block.kind)) block.items.forEach(item => lines.push("- " + [item.label + ":", item.value, plain(item.text)].filter(Boolean).join(" ")));
      else if (block.kind === "proscons") {
        block.pros.forEach(item => lines.push("+ " + plain(item.text)));
        block.cons.forEach(item => lines.push("− " + plain(item.text)));
      } else if (block.kind === "quote") lines.push("> " + plain(block.text));
      else if (block.items?.length) block.items.forEach((item, i) => lines.push((block.kind === "steps" ? i + 1 + "." : "-") + " " + plain(item.text)));
      else lines.push(plain(block.text));
      lines.push("");
    }
    const cited = [...new Set(t.blocks.flatMap(b => [b.citations || [], ...[...(b.items || []), ...(b.pros || []), ...(b.cons || [])].map(i => i.citations || []), ...(b.rows || []).flat().map(c => c.citations || [])].flat()))];
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
    if (!value) turn?.skeletons.forEach(s => s.stop());
    if (header && !isFull()) header.classList.toggle("is-busy", value);
    if (!value) header?.classList.remove("is-busy");
    followup.classList.toggle("is-busy", value);
    sendButton.setAttribute("aria-label", value ? "Stop answering" : "Send follow-up");
    syncComposer();
  };
  // ------------------------------------------------------------ composer
  const depthInput = find("[data-ai-depth]");
  const depthOptions = [...followup.querySelectorAll("[data-depth]")];
  const threadContext = find("[data-ai-thread-context]");
  const freshButton = find("[data-ai-fresh]");
  const charCount = find("[data-ai-char-count]");
  let fresh = false;  // ask without the earlier answer as context
  const setDepth = (value, remember = false) => {
    depthInput.value = value;
    depthOptions.forEach(b => {
      const on = b.dataset.depth === value;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    if (remember) try { localStorage.setItem("archer-ai-depth", value); } catch {}
  };
  try { if (localStorage.getItem("archer-ai-depth") === "compact") setDepth("compact"); } catch {}
  depthOptions.forEach(b => {
    b.addEventListener("click", () => setDepth(b.dataset.depth, true));
    b.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const next = depthOptions[(depthOptions.indexOf(b) + 1) % depthOptions.length];
      setDepth(next.dataset.depth, true);
      next.focus();
    });
  });
  const followable = () => turn?.node.dataset.state === "complete" ? turn.runId : "";
  const syncComposer = () => {
    const text = composerInput.value.trim();
    const parent = followable();
    sendButton.disabled = !busy && !text;
    followup.classList.toggle("has-text", Boolean(text));
    threadContext.hidden = !parent;
    threadContext.classList.toggle("is-fresh", fresh);
    freshButton.setAttribute("aria-pressed", String(fresh));
    freshButton.textContent = fresh ? "Follow up instead" : "New question";
    find("[data-ai-thread-label]").textContent = fresh ? "New question about this search" : "Following up on the answer above";
    composerInput.placeholder = busy ? "Archer is answering. Type your next question…"
      : parent && !fresh ? "Ask a follow-up…" : "Ask anything about this search…";
    const length = composerInput.value.length;
    charCount.hidden = length < 480;
    charCount.textContent = length + " / 600";
  };
  freshButton.addEventListener("click", () => { fresh = !fresh; syncComposer(); composerInput.focus(); });
  syncComposer();

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
      // Suggested next questions belong to AI Mode; Balanced has its follow-up line instead.
      const suggestions = isFull() ? data.followups || [] : [];
      if (suggestions.length) list.append(el("p", { class: "ai-block-label", text: "Ask next" }));
      for (const question of suggestions) {
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
    syncComposer();
    updatePrompt();
  };
  const consume = (event, data) => {
    if (event === "run") { runId = data.id; turn.runId = data.id; turn.depth = data.depth; turn.node.dataset.depth = data.depth; }
    else if (event === "status") { root.dataset.stage = data.stage; turn.status.textContent = data.text; step(data.text, data.stage); }
    else if (event === "sources") renderSources(data.sources);
    else if (event === "notice") notice(data.text);
    else if (event === "card") renderCard(data);
    else if (event === "block") renderBlock(data);
    else if (event === "visuals" && data.verdict) applyVisuals(turn, data);
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
    const parent = fresh ? "" : followable();
    composerInput.value = "";
    composerInput.style.height = "";
    fresh = false;
    start(depthInput.value, { ...(parent ? { parent } : {}), question });
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
  // Suggested questions (the product panel, AI Mode's welcome) open research on that exact question.
  document.querySelectorAll("[data-ai-question]").forEach(button => button.addEventListener("click", () => start("research", { question: button.dataset.aiQuestion })));
  find("[data-ai-back]").addEventListener("click", leaveFullView);
  find("[data-ai-web]").addEventListener("click", leaveFullView);
  document.addEventListener("keydown", event => {
    // "/" jumps to the composer in AI Mode, as it jumps to the search box elsewhere.
    if (event.key === "/" && isFull() && !followup.hidden && !/input|textarea|select/i.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
      event.preventDefault();
      composerInput.focus();
      return;
    }
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
    // Set only by a submit on the home page in AI Mode, for this exact query, and used once.
    let autostart = null;
    try { autostart = sessionStorage.getItem("archer-ai-autostart"); sessionStorage.removeItem("archer-ai-autostart"); } catch {}
    if (autostart && autostart.trim() === (root.dataset.query || "").trim()) start("research");
    else restore();
  } else if (defaultMode === "balanced" && root.dataset.eligible === "true" && consent && configured) {
    // Paid only once the results are actually in front of you: not in a background tab, and not
    // when you click a result straight away (leaving the page cancels the pending start).
    let timer = 0;
    const arm = () => {
      clearTimeout(timer);
      if (document.visibilityState !== "visible") return;
      timer = setTimeout(() => {
        document.removeEventListener("visibilitychange", arm);
        if (!busy && !answered() && document.visibilityState === "visible") start("compact", { automatic: true });
      }, 700);
    };
    document.addEventListener("visibilitychange", arm);
    addEventListener("pagehide", () => clearTimeout(timer));
    arm();
  } else updatePrompt();
});

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
// An external picture, or a VLR.gg one Archer fetches for the page (its CDN refuses a browser with no Referer).
const isHttp = (u) => typeof u === "string" && (/^https?:\/\//.test(u) || u.startsWith("/api/vlr/img/"));

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
  const i = el("img", { src, alt, loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
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

// ------------------------------------------------ rate-limit banner
// Shown on every results page until dismissed; the dismissal covers this set of engines only, so a new
// rate limit brings it back.
const rateBanner = $("[data-rl-banner]");
if (rateBanner) {
  let dismissed = "";
  try { dismissed = localStorage.getItem("archer-rl-dismissed") || ""; } catch { /* storage blocked: always show */ }
  if (dismissed !== rateBanner.dataset.rlKey) {
    // The results page streams it in after the header (its text needs the search); it belongs on top.
    document.body.prepend(rateBanner);
    rateBanner.hidden = false;
    document.body.classList.add("has-rl");
  }
  $("[data-rl-close]", rateBanner).addEventListener("click", () => {
    rateBanner.hidden = true;
    document.body.classList.remove("has-rl");
    try { localStorage.setItem("archer-rl-dismissed", rateBanner.dataset.rlKey); } catch { /* shows again next page */ }
  });
}

// ------------------------------------------------------------- history
// The session's CSRF token rides on the page (results, home, history); history changes need it.
const pageCsrf = () => $("[data-csrf]")?.dataset.csrf || "";
async function historyPost(url, body) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-Archer-CSRF": pageCsrf() },
    body: JSON.stringify(body) });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Archer couldn't update your history.");
  return response.json();
}

// Opened results are remembered with the search they came from (keepalive outlives the navigation).
const historyMain = $("main[data-history]");
if (historyMain) {
  const searchId = Number(historyMain.dataset.history);
  const sent = new Set();
  const remember = (e) => {
    if (e.type === "auxclick" && e.button !== 1) return;
    const link = e.target.closest("a[href]");
    const item = link?.closest("[data-result], .discussion, [data-recall], .news-item");
    if (!item || !isHttp(link.href) || sent.has(link.href)) return;
    sent.add(link.href);
    const all = $$("[data-result], .news-item", historyMain);
    const title = $(".result-title, .discussion-title, .recall-title", item)?.textContent || link.textContent;
    const snippet = $(".result-snippet, .discussion-snippet", item)?.textContent || "";
    fetch("/api/history/click", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ search: searchId || null, url: link.href.slice(0, 2000), title: title.trim().slice(0, 300),
        snippet: snippet.trim().slice(0, 500), position: Math.max(0, all.indexOf(item)) }) }).catch(() => {});
  };
  historyMain.addEventListener("click", remember);
  historyMain.addEventListener("auxclick", remember);
}

// Raise, lower or block a site from a result's menu; the page reloads with the new ranking.
for (const menu of $$("[data-site-menu]")) {
  menu.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-site-pref]");
    if (!button) return;
    button.disabled = true;
    try {
      await historyPost("/api/history/site", { host: menu.dataset.siteMenu, pref: button.dataset.sitePref || null });
      location.reload();
    } catch (error) { button.disabled = false; button.textContent = error.message; }
  });
}
document.addEventListener("click", (e) => {
  for (const open of $$("details.result-menu[open]")) if (!open.contains(e.target)) open.open = false;
});

// "Not related": a wrong match is removed now and never paired with this search again.
for (const section of $$("[data-unrelate-q]")) {
  section.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-unrelate]");
    if (!button) return;
    e.preventDefault();
    button.disabled = true;
    try {
      await historyPost("/api/history/unrelated", { q: section.dataset.unrelateQ, other: button.dataset.unrelate });
      button.closest(".chip-pair, .recall-row")?.remove();
      if (!$(".chip-pair, .recall-row", section)) section.remove();
    } catch (_) { button.disabled = false; }
  });
}

// History page. Removing is undoable for a few seconds: rows leave at once and the delete is sent
// when the toast closes, another removal starts, or the page is left (keepalive). Clearing a range
// goes through a dialog; failures show in the toast, never in alert().
const historyPage = $("[data-history-page]");
if (historyPage) {
  const toast = $("[data-toast]", historyPage), toastText = $("[data-toast-text]", toast), undoButton = $("[data-toast-undo]", toast);
  let toastTimer = 0;
  const say = (text, { tone = "", undo = null, ms = 4000 } = {}) => {
    clearTimeout(toastTimer);
    toastText.textContent = text;
    toast.dataset.tone = tone;
    undoButton.hidden = !undo;
    undoButton.onclick = undo;
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, ms);
  };
  const fail = (error) => say(error.message || "Archer couldn't update your history.", { tone: "error", ms: 6000 });

  // Detach nodes (and any day left empty), remembering where each one was.
  const detach = (nodes) => {
    const spots = [];
    for (const node of nodes) {
      if (!node.isConnected) continue;
      spots.push({ node, parent: node.parentNode, next: node.nextSibling });
      node.remove();
    }
    for (const day of $$(".h-day", historyPage)) {
      if (!$(".h-entry", day)) { spots.push({ node: day, parent: day.parentNode, next: day.nextSibling }); day.remove(); }
    }
    recount();
    return spots;
  };
  const restore = (spots) => {
    for (const s of spots.reverse()) s.parent.insertBefore(s.node, s.next?.isConnected ? s.next : null);
    recount();
  };
  const recount = () => { for (const day of $$(".h-day", historyPage)) { const n = $(".h-day-n", day); if (n) n.textContent = $$(".h-entry", day).length; } };

  let pending = null;
  const send = (batch, keepalive = false) => fetch("/api/history/delete", {
    method: "POST", keepalive, headers: { "Content-Type": "application/json", "X-Archer-CSRF": pageCsrf() },
    body: JSON.stringify({ searches: batch.searches, clicks: batch.clicks }),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Archer couldn't remove that."); });
  const commit = (keepalive = false) => {
    if (!pending) return;
    const batch = pending;
    pending = null;
    clearTimeout(batch.timer);
    send(batch, keepalive).catch((error) => { restore(batch.spots); fail(error); });
  };
  const remove = (searches, clicks, nodes, label) => {
    commit();
    const spots = detach(nodes);
    const batch = { searches, clicks, spots };
    batch.timer = setTimeout(() => { if (pending === batch) { commit(); toast.hidden = true; } }, 6000);
    pending = batch;
    say(label, { ms: 6000, undo: () => {
      if (pending !== batch) return;
      clearTimeout(batch.timer);
      pending = null;
      restore(spots);
      toast.hidden = true;
    } });
    syncPicks();
  };
  addEventListener("pagehide", () => commit(true));

  // Selecting several searches.
  const selbar = $("[data-selbar]", historyPage), selCount = $("[data-sel-count]", selbar);
  const picked = () => $$("[data-pick]:checked", historyPage);
  function syncPicks() {
    const n = picked().length;
    selbar.hidden = n === 0;
    historyPage.classList.toggle("is-picking", n > 0);
    selCount.textContent = `${n} selected`;
    for (const box of $$("[data-pick]", historyPage)) box.closest(".h-entry").classList.toggle("is-picked", box.checked);
  }
  historyPage.addEventListener("change", (e) => { if (e.target.matches("[data-pick]")) syncPicks(); });
  $("[data-sel-cancel]", selbar).addEventListener("click", () => { picked().forEach((b) => { b.checked = false; }); syncPicks(); });
  $("[data-sel-remove]", selbar).addEventListener("click", () => {
    const boxes = picked();
    boxes.forEach((b) => { b.checked = false; });
    remove(boxes.map((b) => Number(b.dataset.pick)), [], boxes.map((b) => b.closest(".h-entry")),
      `Removed ${boxes.length} search${boxes.length === 1 ? "" : "es"}`);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && picked().length && !$("dialog[open]")) { picked().forEach((b) => { b.checked = false; }); syncPicks(); }
  });

  historyPage.addEventListener("click", async (e) => {
    const removeSearch = e.target.closest("[data-remove-search]");
    const removeClick = e.target.closest("[data-remove-click]");
    const siteSet = e.target.closest("[data-site-set]");
    if (removeSearch) {
      remove([Number(removeSearch.dataset.removeSearch)], [], [removeSearch.closest(".h-entry")], "Removed from history");
    } else if (removeClick) {
      remove([], [Number(removeClick.dataset.removeClick)], [removeClick.closest(".h-click")], "Page removed from history");
    } else if (siteSet) {
      const row = siteSet.closest("[data-site]"), pref = siteSet.dataset.siteSet || null;
      try {
        await historyPost("/api/history/site", { host: row.dataset.site, pref });
        $$("[data-site-set]", row).forEach((b) => b.setAttribute("aria-pressed", String(b === siteSet)));
        const said = { raise: "will rank higher", lower: "will rank lower", block: "is hidden from your results" }[pref] || "ranks normally again";
        say(`${row.dataset.site} ${said}`);
      } catch (error) { fail(error); }
    }
  });

  // Clear a time range.
  const dialog = $("[data-clear-dialog]", historyPage), confirmClear = $("[data-clear-confirm]", dialog), clearError = $("[data-clear-error]", dialog);
  $("[data-history-clear-open]", historyPage)?.addEventListener("click", () => { clearError.hidden = true; dialog.showModal(); });
  confirmClear.addEventListener("click", async () => {
    const range = $("input[name=range]:checked", dialog).value;
    confirmClear.disabled = true;
    commit();
    try { await historyPost("/api/history/clear", { range }); location.reload(); }
    catch (error) { confirmClear.disabled = false; clearError.textContent = error.message; clearError.hidden = false; }
  });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
}

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
    // An empty box shows your recent searches; typing shows suggestions with your own matches first.
    const empty = !draft.trim();
    if (composing || (!empty && !eligible(draft)) || document.activeElement !== input) return;
    const requested = draft, ticket = generation;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const mine = fetch(`/api/history/recent?q=${encodeURIComponent(requested)}`, { signal: controller.signal, cache: "no-store" })
          .then((r) => (r.ok ? r.json() : {})).then((d) => (Array.isArray(d.items) ? d.items : [])).catch(() => []);
        let data = [requested, []];
        if (!empty) {
          const response = await fetch(`/suggest?q=${encodeURIComponent(requested)}`, { signal: controller.signal, cache: "no-store" });
          if (response.ok) data = await response.json();
        }
        const history = await mine;
        if (ticket !== generation || composing || document.activeElement !== input || input.value !== requested) return;
        if (!Array.isArray(data) || data[0] !== requested || !Array.isArray(data[1])) return;
        const past = new Set(history.map((s) => String(s).toLocaleLowerCase()));
        terms = [...new Set([...history, ...data[1]].filter((s) => typeof s === "string" && s.trim() && s.length <= Number(wrapper.dataset.suggestMax)))].slice(0, 8);
        if (!terms.length) return;
        list.replaceChildren(...terms.map((term, i) => {
          const row = el("div", { id: `${list.id}-${i}`, class: "suggestion", role: "option", "aria-selected": "false" });
          const isPast = past.has(term.toLocaleLowerCase());
          const label = el("span", { class: "suggestion-text" });
          const match = term.toLocaleLowerCase().indexOf(requested.trim().toLocaleLowerCase());
          if (match < 0) label.textContent = term;
          else label.append(document.createTextNode(term.slice(0, match)),
            el("strong", { text: term.slice(match, match + requested.trim().length) }),
            document.createTextNode(term.slice(match + requested.trim().length)));
          const searchIcon = icon(isPast ? "clock" : "search");
          searchIcon.setAttribute("aria-hidden", "true");
          if (!isPast) $("use", searchIcon).setAttribute("href", "#i-search");
          row.append(searchIcon, label);
          if (isPast) {
            row.classList.add("suggestion--past");
            const remove = el("button", { type: "button", class: "suggestion-remove", "aria-label": `Remove “${term}” from history`, title: "Remove from history" });
            const x = icon("x");
            $("use", x).setAttribute("href", "#i-x");
            remove.append(x);
            remove.addEventListener("click", async (e) => {
              e.stopPropagation();
              try { await historyPost("/api/history/forget", { q: term }); } catch (_) { return; }
              const at = terms.indexOf(term);
              if (at >= 0) terms.splice(at, 1);
              row.remove();
              [...list.children].forEach((r, n) => { r.id = `${list.id}-${n}`; });
              active = -1;
              if (!terms.length) close();
              input.focus();
            });
            row.append(remove);
          }
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
      touchStart = row && !e.target.closest(".suggestion-remove") ? { x: e.clientX, y: e.clientY, value: terms[[...list.children].indexOf(row)], moved: false } : null;
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
  // Home's autofocus can occur before this deferred script installs listeners. An empty autofocused
  // box stays closed (home already lists recent searches); a click or arrow key opens them.
  if (document.activeElement === input && input.value.trim()) schedule();
  input.addEventListener("pointerdown", () => { if (document.activeElement === input && list.hidden && !input.value.trim()) setTimeout(schedule, 0); });
  input.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" && list.hidden && !input.value.trim()) schedule(); });
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
  if (e.key === "/" && box && !document.body.classList.contains("ai-fullview") && document.activeElement !== box && !/input|textarea|select/i.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
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
// A news story the engine sent without a picture borrows the article's own share image.
const leadImage = (url) => getJSON(`/api/news/thumb?url=${encodeURIComponent(url)}`).then((d) => (d && isHttp(d.thumb) ? d.thumb : ""));

function card(kind, item) {
  const media = el("div", { class: "card-img" + (item.thumb ? "" : " card-img--empty") });
  const picture = img(item.thumb);
  if (picture) media.append(picture); else media.textContent = kind === "news" ? "📰" : "▶";
  if (kind === "news" && !picture) {
    // Swapped in only once it loads, so a dead link never replaces the placeholder with nothing.
    leadImage(item.url).then((thumb) => (thumb ? firstLoading([{ thumb }]) : null)).then((ok) => {
      const found = ok && img(ok.thumb);
      if (!found) return;
      media.classList.remove("card-img--empty");
      media.replaceChildren(found);
    });
  }
  if (kind === "videos" && item.length) media.append(el("span", { class: "length", text: item.length }));
  if (kind === "images") return el("a", { class: "card", href: item.url, title: item.title }, media);
  return el("a", { class: "card", href: item.url },
    media,
    el("div", { class: "card-body" },
      el("span", { class: "card-host", text: item.host }),
      el("span", { class: "card-title", text: item.title }),
      item.ago ? el("span", { class: "card-age", text: item.ago }) : null));
}

// Strips scroll sideways inside a viewport: arrows page through them, and the edge that has more
// to show fades out instead of cutting a card off.
function scroller(row) {
  if (row.parentElement.classList.contains("strip-view")) return;
  const view = el("div", { class: "strip-view" });
  row.replaceWith(view);
  const arrow = (dir, label) => {
    const b = el("button", { type: "button", class: `strip-nav strip-nav--${dir}`, "aria-label": label, tabindex: "-1" }, icon("chevron"));
    b.addEventListener("click", () => {
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      row.scrollBy({ left: (dir === "next" ? 1 : -1) * row.clientWidth * 0.85, behavior: still ? "auto" : "smooth" });
    });
    return b;
  };
  view.append(row, arrow("prev", "Scroll left"), arrow("next", "Scroll right"));
  const update = () => {
    const max = row.scrollWidth - row.clientWidth;
    view.classList.toggle("can-prev", row.scrollLeft > 4);
    view.classList.toggle("can-next", row.scrollLeft < max - 4);
  };
  row.addEventListener("scroll", update, { passive: true });
  new ResizeObserver(update).observe(row);
  new MutationObserver(update).observe(row, { childList: true });
  update();
}
for (const row of $$(".strip-row")) scroller(row);

for (const section of $$("[data-lazy]")) {
  const kind = section.dataset.lazy;
  strip(kind, section.dataset.q).then((items) => {
    items = items.filter((i) => isHttp(i.url) && (kind === "news" || i.thumb));
    if (kind === "news") items = [...items.filter((i) => i.thumb), ...items.filter((i) => !i.thumb)];
    if (items.length < 2) return section.remove();
    $(".strip-row", section).replaceChildren(...items.slice(0, kind === "images" ? 16 : 10).map((i) => card(kind, i)));
  });
}

// ---------------------------------------------------- product panel
// Gallery, tabs and "show all"; then /api/product adds listings and reviews from a price search and a
// review search, and the image search adds pictures. Server markup is kept when nothing new arrives.
function hostBadge(host) {
  const parts = host.split(".");
  const second = new Set(["co", "com", "org", "net", "ac", "gov", "edu", "nic", "gen", "ne", "or"]);
  const raw = parts.length >= 3 && second.has(parts.at(-2)) ? parts.at(-3) : parts.length >= 2 ? parts.at(-2) : host;
  const name = raw.charAt(0).toUpperCase() + raw.slice(1);
  let crc = ~0;
  for (const byte of new TextEncoder().encode(name)) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) crc = crc & 1 ? (crc >>> 1) ^ 0xEDB88320 : crc >>> 1;
  }
  const badge = el("span", { class: "badge" }, (name[0] || "?").toUpperCase());
  badge.style.setProperty("--h", String((~crc >>> 0) % 360));
  const fav = el("img", { class: "badge-icon", src: "/api/favicon/" + host, alt: "", loading: "lazy" });
  badge.append(fav);
  wireFavicon(fav);
  return badge;
}

const SPEC_ICONS = [[/display|screen/i, "layers"], [/chip|processor|cpu|gpu/i, "cube"], [/battery|charg/i, "timer"],
  [/camera|lens/i, "target"], [/storage|memory|ram/i, "package"], [/weight|mass/i, "scale"], [/size|dimension/i, "ruler"],
  [/price|cost/i, "coins"], [/\bos\b|software|system/i, "code"], [/release|launch/i, "calendar"], [/connect|bluetooth|wi-?fi|port/i, "link"]];
const specIcon = (label) => (SPEC_ICONS.find(([re]) => re.test(label)) || [null, "info"])[1];

function scoreRing(score, small) {
  const band = score >= 8 ? "is-high" : score >= 6.5 ? "is-mid" : "is-low";
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 36 36");
  for (const [cls, dash] of [["pd-ring-track", null], ["pd-ring-fill", score * 10]]) {
    const c = document.createElementNS(ns, "circle");
    for (const [k, v] of [["class", cls], ["cx", 18], ["cy", 18], ["r", 15.5]]) c.setAttribute(k, v);
    if (dash != null) { c.setAttribute("pathLength", "100"); c.setAttribute("stroke-dasharray", dash.toFixed(1) + " 100"); }
    svg.append(c);
  }
  return el("span", { class: `pd-ring${small ? " pd-ring--sm" : ""} ${band}`, "aria-hidden": "true" }, svg, el("span", { text: String(score) }));
}

const PD_ROLE = { official: "Official store", store: "Store", guide: "Price tracker", used: "Used or refurbished" };
function offerRow(o) {
  const meta = [o.variant, PD_ROLE[o.role] || "Store", o.stock, o.rating ? "★ " + o.rating : ""].filter(Boolean).join(" · ");
  const link = el("a", { href: o.url, rel: "noopener noreferrer" },
    hostBadge(o.host),
    el("span", { class: "pd-offer-store" }, el("strong", { text: o.store }), el("small", { text: meta })),
    o.best ? el("span", { class: "pd-tag", text: "Lowest" }) : null,
    el("span", { class: "pd-offer-price" }, o.price_text ? document.createTextNode(o.price_text) : el("span", { class: "muted", text: "See price" })));
  const go = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-ext");
  go.setAttribute("class", "pd-offer-go");
  go.append(use);
  link.append(go);
  return el("li", { class: "pd-offer" + (o.best ? " is-best" : ""), "data-pd-role": o.role }, link);
}

function reviewCard(r) {
  return el("a", { class: "pd-review", href: r.url, rel: "noopener noreferrer" },
    el("span", { class: "pd-review-head" }, hostBadge(r.host),
      el("span", {}, el("strong", { text: r.site }), r.date ? el("small", { text: r.date }) : null),
      r.score != null ? scoreRing(r.score, true) : null),
    el("span", { class: "pd-review-title", text: r.title }),
    r.excerpt ? el("span", { class: "pd-review-text", text: r.excerpt }) : null);
}

function productPanel(panel) {
  const stage = $("[data-pd-main]", panel);
  const frame = stage.parentElement;
  const thumbs = $("[data-pd-thumbs]", panel);
  const shown = () => $$(".pd-thumb", thumbs);
  const sync = () => { thumbs.hidden = shown().length < 2; };
  const credit = $("[data-pd-credit]", panel);
  const loaded = (button) => {
    frame.classList.remove("is-loading");
    $("[data-pd-empty]", panel)?.remove();
    // Every photo says where it came from and links there (the maker's page, a store listing, a review).
    const { pdUrl: url, pdSource: source } = button.dataset;
    credit.hidden = !(isHttp(url) && source);
    if (!credit.hidden) { credit.href = url; credit.textContent = "Photo · " + source; }
  };
  // Full-size picture on the stage, falling back to the thumbnail; a picture that fails drops out.
  // Only the latest request lands, so a slow first picture can't replace a later click.
  let wanted = null;
  const show = (button) => {
    if (!button) return;
    wanted = button;
    shown().forEach((b) => b.classList.toggle("is-active", b === button));
    const { pdFull: full, pdThumb: small } = button.dataset;
    if (stage.dataset.src && [full, small].includes(stage.dataset.src)) return;
    stage.classList.add("is-swapping");
    const attempt = (src, next) => {
      const probe = new Image();
      probe.referrerPolicy = "no-referrer";
      probe.onload = () => {
        if (wanted !== button) return;
        if (probe.naturalWidth < 80 && next) return attempt(next, null);
        wanted = null;
        stage.src = src;
        stage.dataset.src = src;
        stage.hidden = false;
        stage.classList.remove("is-swapping");
        loaded(button);
      };
      probe.onerror = () => {
        if (wanted !== button) return;
        if (next) return attempt(next, null);
        wanted = null;
        stage.classList.remove("is-swapping");
        button.remove();
        sync();
        if (!stage.dataset.src) show(shown()[0]);
      };
      probe.src = src;
    };
    attempt(isHttp(full) ? full : small, isHttp(full) && full !== small ? small : null);
  };
  const wire = (button) => {
    button.addEventListener("click", () => show(button));
    $("img", button)?.addEventListener("error", () => { button.remove(); sync(); });
  };
  stage.dataset.src = stage.getAttribute("src") || "";
  stage.addEventListener("error", () => {
    shown().find((b) => [b.dataset.pdFull, b.dataset.pdThumb].includes(stage.dataset.src))?.remove();
    stage.dataset.src = "";
    stage.hidden = true;
    sync();
    show(shown()[0]);
  });
  shown().forEach(wire);
  const addPicture = (small, full, url = "", source = "") => {
    if (!isHttp(small) || shown().length >= 7 || shown().some((b) => b.dataset.pdThumb === small || (full && b.dataset.pdFull === full))) return;
    const button = el("button", { type: "button", class: "pd-thumb", "data-pd-thumb": small, "data-pd-full": isHttp(full) ? full : small,
      "data-pd-url": url, "data-pd-source": source, "aria-label": source ? "Photo from " + source : "Picture " + (shown().length + 1),
      title: source || null }, img(small));
    thumbs.append(button);
    wire(button);
    sync();
    if (!stage.dataset.src && !wanted) show(button);
  };

  // Tabs: Overview / Reviews / Specs.
  const tabs = $$("[data-pd-tab]", panel);
  const select = (name) => {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.pdTab === name)));
    $$("[data-pd-panel]", panel).forEach((p) => { p.hidden = p.dataset.pdPanel !== name; });
  };
  tabs.forEach((t) => t.addEventListener("click", () => select(t.dataset.pdTab)));
  $$("[data-pd-goto]", panel).forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    select(a.dataset.pdGoto);
    $(".pd-tabs", panel).scrollIntoView({ block: "nearest", behavior: "smooth" });
  }));
  const offers = $("[data-pd-offers]", panel);
  const more = $("[data-pd-more]", panel);
  more.addEventListener("click", () => { offers.classList.remove("is-clamped"); more.hidden = true; more.dataset.opened = "1"; });

  const price = (data) => {
    const box = $("[data-pd-price]", panel);
    if (!data.price) {
      // Done looking: say so instead of leaving "Checking stores…" up.
      const main = $(".pd-price-main--none strong", box);
      if (main) main.textContent = panel.dataset.used ? "No new price found" : "No price listed";
      return;
    }
    const p = data.price;
    const best = data.offers.find((o) => o.best);
    const sub = p.listed ? "From price trackers; no store page shows a price yet"
      : p.stores > 1 ? `Across ${p.stores} stores` + (best ? ` · lowest at ${best.store}` : "") : "From one store's product page";
    box.replaceChildren(
      el("p", { class: "pd-eyebrow", text: p.listed ? "Listed price" : "Price" }),
      el("p", { class: "pd-price-main" }, el("strong", { text: p.low_text }), p.high_text ? el("span", { class: "pd-price-to", text: "to " + p.high_text }) : null),
      el("p", { class: "pd-price-sub" }, p.variant ? el("b", { text: p.variant }) : null, document.createTextNode((p.variant ? " · " : "") + sub)));
  };
  const scores = (data) => {
    const box = $("[data-pd-scores]", panel);
    const parts = [];
    if (data.score) {
      const a = el("a", { class: "pd-score", href: "#pd-reviews" }, scoreRing(data.score.value),
        el("span", {}, el("strong", { text: "Expert score" }), el("small", { text: `${data.score.count} review${data.score.count === 1 ? "" : "s"} · out of 10` })));
      a.addEventListener("click", (e) => { e.preventDefault(); select("reviews"); });
      parts.push(a);
    }
    if (data.rating) {
      const stars = el("span", { class: "pd-stars", "aria-hidden": "true" });
      stars.style.setProperty("--v", data.rating.value);
      parts.push(el("div", { class: "pd-score" }, stars, el("span", {},
        el("strong", {}, document.createTextNode(data.rating.value + " "), el("span", { class: "muted", text: "/ 5" })),
        el("small", { text: "Store rating" + (data.rating.votes ? " · " + data.rating.votes.toLocaleString("en-IN") + " ratings" : "") }))));
    }
    if (!parts.length) return;
    box.replaceChildren(...parts);
    box.hidden = false;
  };
  const chips = (holder, items, make) => {
    const row = $(".pd-chips", holder);
    const have = new Set($$(".pd-chip", row).map((c) => c.textContent.trim().toLowerCase()));
    for (const item of items) {
      const node = make(item);
      if (!have.has(node.textContent.trim().toLowerCase())) { row.append(node); have.add(node.textContent.trim().toLowerCase()); }
    }
    holder.hidden = !row.children.length;
  };

  const { name, q, brand, alias, used, stale } = panel.dataset;
  const live = getJSON(`/api/product?name=${encodeURIComponent(name)}&q=${encodeURIComponent(q)}&brand=${encodeURIComponent(brand)}&alias=${encodeURIComponent(alias)}${used ? "&used=1" : ""}${stale ? "&stale=1" : ""}`);
  live.then((data) => {
    if (!data || Array.isArray(data)) return;
    const fresh = data.offers.filter((o) => o.role !== "used");
    const current = $$(".pd-offer", offers);
    const pricedNow = current.filter((li) => !$(".pd-offer-price .muted", li)).length;
    if (fresh.length > current.length || fresh.filter((o) => o.price_text).length > pricedNow) {
      offers.replaceChildren(...fresh.map(offerRow));
      const clamp = fresh.length > 5 && panel.dataset.intent !== "price" && !more.dataset.opened;
      offers.classList.toggle("is-clamped", clamp);
      more.textContent = `Show all ${fresh.length} listings`;
      more.hidden = !clamp;
      price(data);
    } else if (!data.price) price(data);
    scores(data);
    // Second-hand listings and their own price line; the search links stay as rendered.
    const usedBox = $("[data-pd-used]", panel);
    const usedOffers = data.offers.filter((o) => o.role === "used");
    if (usedOffers.length) $("[data-pd-used-offers]", panel).replaceChildren(...usedOffers.map(offerRow));
    const usedSum = $("[data-pd-used-sum]", panel);
    if (data.used) usedSum.replaceChildren(document.createTextNode("Used from "), el("b", { text: data.used.low_text }),
      document.createTextNode(` · ${data.used.count} listing${data.used.count === 1 ? "" : "s"}`));
    else if (usedOffers.length) usedSum.textContent = "Second-hand and refurbished listings; prices are on each page.";
    else if (used) usedSum.textContent = "No second-hand listings turned up in the results; try the marketplace search.";
    usedBox.hidden = !(used || usedOffers.length);
    const reviews = $("[data-pd-reviews]", panel);
    if (data.reviews.length >= $$(".pd-review", reviews).length && data.reviews.length) {
      reviews.replaceChildren(...data.reviews.map(reviewCard));
    } else if (!data.reviews.length && !$$(".pd-review", reviews).length) {
      $("[data-pd-reviews-empty]", panel)?.replaceChildren(document.createTextNode("No expert reviews turned up in the results."));
    }
    $("[data-pd-review-count]", panel).textContent = $$(".pd-review", reviews).length || "";
    if (data.highlights.length) {
      const box = $("[data-pd-highlights]", panel);
      const list = $("ul", box);
      const have = new Set($$("li", list).map((li) => li.textContent.toLowerCase()));
      data.highlights.filter((h) => !have.has(h.toLowerCase())).slice(0, 6 - have.size).forEach((h) => list.append(el("li", { text: h })));
      box.hidden = !list.children.length;
      const specsTab = tabs.find((t) => t.dataset.pdTab === "specs");
      if (specsTab?.hidden) {
        specsTab.hidden = false;
        $("[data-pd-panel='specs']", panel).prepend(el("p", { class: "pd-label", text: "From store listings" }), el("ul", { class: "pd-spec-list" }, ...data.highlights.map((h) => el("li", { text: h }))));
      }
    }
    const variants = $("[data-pd-variants]", panel);
    chips($("[data-pd-storage]", panel), data.variants.storage, (s) => el("span", { class: "pd-chip", text: s }));
    chips($("[data-pd-colors]", panel), data.variants.colors, (c) => {
      const dot = el("i");
      dot.style.setProperty("--c", c.hex);
      return el("span", { class: "pd-chip pd-colour" }, dot, document.createTextNode(c.name));
    });
    variants.hidden = $("[data-pd-storage]", panel).hidden && $("[data-pd-colors]", panel).hidden;
    const quotes = $("[data-pd-quotes]", panel);
    const said = data.reviews.filter((r) => r.excerpt).slice(0, 3);
    if (said.length > $$(".pd-quote", quotes).length) {
      quotes.replaceChildren(el("p", { class: "pd-label", text: "What reviewers say" }), ...said.map((r) => el("blockquote", { class: "pd-quote" },
        el("p", { text: r.excerpt }),
        el("footer", {}, hostBadge(r.host), el("a", { href: r.url, rel: "noopener noreferrer", text: r.site }), r.score_text ? el("span", { class: "muted", text: "· " + r.score_text }) : null))));
      quotes.hidden = false;
    }
  });
  // Photos from where it's sold: the maker's site, then stores, then review sites (/api/product/photos);
  // product-page thumbnails only fill in when fewer than three turn up.
  const pictures = getJSON(`/api/product/photos?name=${encodeURIComponent(name)}&brand=${encodeURIComponent(brand)}&alias=${encodeURIComponent(alias)}`);
  pictures.then((items) => (Array.isArray(items) ? items : []).forEach((i) => addPicture(i.thumb, i.full, i.url, i.source)));
  Promise.all([pictures, live]).then(([, data]) => {
    if (shown().length < 3 && data && !Array.isArray(data)) data.offers.filter((o) => o.thumb).forEach((o) => addPicture(o.thumb, o.thumb, o.url, o.store));
    if (!shown().length && !stage.dataset.src) frame.classList.remove("is-loading");
  });
  // Video reviews of this model only: titles must carry its model number (or every word of its name).
  const videos = $("[data-pd-videos]", panel);
  const key = (t) => t.toLowerCase().replace(/[^a-z0-9]/g, "");
  const models = name.split(/\s+/).filter((w) => /\d/.test(w)).map(key);
  const needed = models.length ? models : name.split(/\s+/).map(key);
  strip("videos", name + " review").then((items) => {
    items = items.filter((i) => isHttp(i.url) && i.thumb && needed.every((w) => key(i.title).includes(w)));
    if (items.length < 2) return;
    $(".strip-row", videos).replaceChildren(...items.slice(0, 8).map((i) => card("videos", i)));
    videos.hidden = false;
  });
}
$$("[data-product]").forEach(productPanel);

// ------------------------------------------------ video game panel
const wholePicture = (pic) => pic.classList.toggle("is-whole", /\.svg(\?|$)/i.test(pic.src) || pic.naturalWidth < pic.naturalHeight * 1.3);

// The bento resolves itself: the picture beside the price and ratings cards wants a 16:9 height. When those
// cards are much shorter than that, the fact tiles join their column and fill it; otherwise the tiles share
// a full-width row below. Decided from widths only, so moving the tiles can't flip the decision back.
function layoutGame(panel) {
  const grid = $(".gm-grid", panel), side = $(".gm-side", panel), media = $(".gm-media", panel), tiles = $(".gm-tiles", panel);
  if (!grid || !tiles) return;
  const place = () => {
    const stacked = getComputedStyle(grid).gridTemplateColumns.split(" ").length < 2;
    const cards = $$(".gm-card", side).reduce((h, c) => h + c.offsetHeight, 0) + 12 * ($$(".gm-card", side).length - 1);
    const shots = $("[data-gm-shots]", panel);
    const want = media.clientWidth * 9 / 16 + (shots && !shots.hidden ? shots.offsetHeight + 8 : 0);
    const inSide = !stacked && cards < want - 110;
    if (inSide !== tiles.parentElement.classList.contains("gm-side")) {
      if (inSide) side.append(tiles); else grid.after(tiles);
    }
    panel.classList.toggle("gm-tiles-in-side", inSide);
  };
  let width = 0;
  new ResizeObserver(() => { if (panel.clientWidth !== width) { width = panel.clientWidth; place(); } }).observe(panel);
  place();
  return place;
}
// /api/game adds Steam's price, reviews, players, screenshots and PC requirements, and CheapShark's PC deals.
const gameCount = (n) => Number(n).toLocaleString("en-IN");
function gamePanel(panel) {
  const priceBox = $("[data-gm-price]", panel);
  const url = `/api/game?appid=${encodeURIComponent(panel.dataset.gameAppid || "")}&title=${encodeURIComponent(panel.dataset.gameTitle || "")}`;
  // No trailer or cover from Wikidata: the game's best image-search picture fills the stage.
  const fillStage = () => {
    const holder = $(".gm-placeholder", panel);
    if (!holder) return;
    strip("images", `${panel.dataset.gameTitle} video game`).then((items) => {
      const pick = (items || []).find((i) => isHttp(i.full) || isHttp(i.thumb));
      if (!pick || !holder.isConnected) return;
      const pic = el("img", { class: "gm-cover", src: isHttp(pick.full) ? pick.full : pick.thumb, alt: panel.dataset.gameTitle, referrerpolicy: "no-referrer", "data-gm-cover": "" });
      pic.addEventListener("error", () => { if (isHttp(pick.thumb) && pic.src !== pick.thumb) pic.src = pick.thumb; });
      holder.replaceWith(pic);
    });
  };
  // A square or tall picture (a logo, box art) is shown whole; wide art and screenshots fill the stage.
  panel.addEventListener("load", (event) => {
    const pic = event.target;
    if (pic.classList && pic.classList.contains("gm-cover")) wholePicture(pic);
  }, true);
  $$(".gm-cover", panel).forEach((pic) => { if (pic.complete && pic.naturalWidth) wholePicture(pic); });
  const relayout = layoutGame(panel) || (() => {});
  getJSON(url).then((d) => {
    requestAnimationFrame(relayout);  // the cards have their final height once this fills them
    if (!d || !d.appid) {
      priceBox.replaceChildren(el("span", { class: "muted", text: "Not sold on Steam. Prices are on the stores below." }));
      fillStage();
      return;
    }
    const steamLink = isHttp(d.url) ? d.url : null;
    // Price on Steam, in the home currency, with any discount.
    if (d.price && d.price.free) priceBox.replaceChildren(el("strong", { class: "gm-price-now", text: "Free to play" }));
    else if (d.price) {
      priceBox.replaceChildren(...[el("strong", { class: "gm-price-now", text: d.price.text }),
        d.price.discount ? el("span", { class: "gm-cut", text: `−${d.price.discount}%` }) : null,
        d.price.original ? el("s", { class: "muted", text: d.price.original }) : null,
        el("a", { class: "gm-price-where", href: steamLink, text: "on Steam" })].filter(Boolean));
    } else priceBox.replaceChildren(el("span", { class: "muted", text: d.coming_soon ? `Coming soon${d.release ? " · " + d.release : ""}` : "No Steam price in your region" }));

    // Best current PC deals across stores, and the lowest price ever.
    const deals = (d.deals && d.deals.deals) || [];
    const list = $("[data-gm-deals]", panel);
    deals.slice(0, 3).forEach((deal, i) => {
      if (!isHttp(deal.url)) return;
      list.append(el("li", { class: i ? "" : "is-best" }, el("a", { href: deal.url, rel: "noopener noreferrer" },
        el("span", { class: "gm-deal-store" }, el("strong", { text: deal.store }), i ? null : el("small", { text: "Best PC deal" })),
        deal.cut ? el("span", { class: "gm-cut", text: `−${deal.cut}%` }) : null,
        el("span", { class: "gm-deal-price" }, el("strong", { text: deal.usd }), deal.home ? el("small", { text: "≈ " + deal.home }) : null))));
    });
    const lowest = d.deals && d.deals.lowest;
    if (lowest) list.append(el("li", { class: "gm-lowest", text: `Lowest ever ${lowest.usd}${lowest.home ? " (≈ " + lowest.home + ")" : ""}${lowest.date ? " · " + lowest.date : ""}` }));
    list.hidden = !list.children.length;

    // Steam's user reviews as a bar, players right now, and Metacritic when Wikidata had no score.
    const reviews = $("[data-gm-reviews]", panel);
    if (d.reviews && d.reviews.total) {
      const bar = el("span", { class: "gm-bar" }, el("i"));
      bar.firstChild.style.width = Math.max(2, Math.min(100, d.reviews.percent)) + "%";
      reviews.classList.add(d.reviews.percent >= 70 ? "is-good" : d.reviews.percent >= 40 ? "is-mixed" : "is-bad");
      reviews.append(el("a", { href: steamLink ? steamLink + "#app_reviews_hash" : null },
        el("strong", { text: d.reviews.label }), el("span", { text: ` · ${d.reviews.percent}% of ${gameCount(d.reviews.total)} Steam reviews` })), bar);
      reviews.hidden = false;
    }
    const critics = $("[data-gm-critics]", panel);
    if (d.metacritic && !$$(".gm-critic span", critics).some((s) => s.textContent === "Metacritic")) {
      critics.prepend(el("a", { class: "gm-critic", href: isHttp(d.metacritic.url) ? d.metacritic.url : null },
        el("strong", { text: String(d.metacritic.score) }), el("small", { text: "/100" }), el("span", { text: "Metacritic" })));
    }
    if (Number.isFinite(d.players) && d.players > 0) {
      const players = $("[data-gm-players]", panel);
      players.append(el("i", { class: "gm-live", "aria-hidden": "true" }), document.createTextNode(`${gameCount(d.players)} playing on Steam right now`));
      players.hidden = false;
    }

    // Steam features (co-op, controller support…), PC systems, DLC and achievements as chips.
    const features = $("[data-gm-features]", panel);
    // Single-player and multiplayer are already in the Game modes tile; Windows is a given on Steam.
    const extra = (d.features || []).filter((f) => !/^(Single-player|Multiplayer|Family Sharing)$/.test(f)).slice(0, 4);
    [...extra, ...(d.systems || []).filter((s) => s !== "Windows").map((s) => "Runs on " + s), d.dlc ? `${d.dlc} DLC` : ""]
      .filter(Boolean).forEach((f) => features.append(el("span", { class: "tag-chip gm-feature", text: f })));

    // Screenshots: thumbnails under the stage; picking one shows it, the trailer thumb goes back.
    const stage = $("[data-gm-stage]", panel);
    const shots = $("[data-gm-shots]", panel);
    const first = stage.firstElementChild;
    const show = (node, thumb) => {
      stage.replaceChildren(node);
      $$(".gm-shot", shots).forEach((t) => t.classList.toggle("is-active", t === thumb));
    };
    const pics = (d.screenshots || []).filter((s) => isHttp(s.full) && isHttp(s.thumb));
    if (pics.length) {
      if (first && !first.matches("[data-gm-cover]")) {
        const back = el("button", { type: "button", class: "gm-shot gm-shot--video is-active", "aria-label": "Trailer" }, img(($("img", first) || {}).src || pics[0].thumb), icon("film"));
        back.addEventListener("click", () => show(first, back));
        shots.append(back);
      } else if (first && !first.matches("img") && isHttp(d.header)) {
        first.replaceWith(el("img", { class: "gm-cover", src: d.header, alt: "", referrerpolicy: "no-referrer", "data-gm-cover": "" }));
      }
      for (const s of pics) {
        const thumb = el("button", { type: "button", class: "gm-shot", "aria-label": "Screenshot" }, img(s.thumb));
        thumb.addEventListener("click", () => show(el("img", { class: "gm-cover", src: s.full, alt: "Screenshot", referrerpolicy: "no-referrer" }), thumb));
        shots.append(thumb);
      }
      shots.hidden = false;
    }

    if (!pics.length && !isHttp(d.header)) fillStage();

    // PC requirements, minimum against recommended.
    const req = d.requirements || {};
    const labels = [...new Set([...(req.minimum || []), ...(req.recommended || [])].map((r) => r[0]))];
    if (labels.length) {
      const value = (rows, label) => ((rows || []).find((r) => r[0] === label) || [null, "—"])[1];
      const table = el("table", { class: "gm-reqs-table" },
        el("thead", {}, el("tr", {}, el("th", {}), el("th", { text: "Minimum" }), req.recommended && req.recommended.length ? el("th", { text: "Recommended" }) : null)),
        el("tbody", {}, ...labels.map((l) => el("tr", {}, el("th", { text: l }), el("td", { text: value(req.minimum, l) }),
          req.recommended && req.recommended.length ? el("td", { text: value(req.recommended, l) }) : null))));
      $("[data-gm-reqs-body]", panel).append(table);
      $("[data-gm-reqs]", panel).hidden = false;
    }
  });
}
$$("[data-game]").forEach(gamePanel);

// ------------------------------------------------- Formula One and circuits
// Driver, team, Grand Prix and circuit panels. The page arrives with skeletons of the right size; the
// live half lands from /api/f1* and is drawn in the brand's own vocabulary: rounded-square dots at a few
// stepped ink levels, DM Mono labels, and one accent — the team's colour, or plain ink when there is none.
const SVG_NS = "http://www.w3.org/2000/svg";
function svgNode(tag, attrs = {}, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) node.setAttribute(k, v);
  for (const c of kids) if (c) node.append(c);
  return node;
}
const f1Num = (n) => Number(n || 0).toLocaleString("en-US");
const f1When = (iso, opts) => { const d = new Date(iso); return Number.isNaN(+d) ? "" : d.toLocaleString(undefined, opts); };
const f1Day = (iso) => f1When(iso, { weekday: "short", day: "numeric", month: "short" });
const f1Clock = (iso) => f1When(iso, { hour: "2-digit", minute: "2-digit" });
const f1Plural = (n, one, many = one + "s") => `${f1Num(n)} ${Number(n) === 1 ? one : many}`;
const F1_SESSION_LABEL = { FirstPractice: "Practice 1", SecondPractice: "Practice 2", ThirdPractice: "Practice 3",
  SprintQualifying: "Sprint qualifying", SprintShootout: "Sprint shootout", Sprint: "Sprint", Qualifying: "Qualifying", Race: "Race" };

function f1Label(text, iconName) {
  return el("p", { class: "f1-label" }, icon(iconName), el("span", { text }));
}

// "2008 · 2014–15 · 2017–20": championship years with runs folded together.
function f1Years(years) {
  const runs = [];
  for (const y of [...years].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && y === last[1] + 1) last[1] = y; else runs.push([y, y]);
  }
  return runs.map(([a, b]) => (a === b ? String(a) : `${a}–${String(b).slice(String(a).slice(0, 2) === String(b).slice(0, 2) ? 2 : 0)}`)).join(" · ");
}

// A tooltip that follows whatever it is pinned to inside a card. One per card, reused.
function f1Tip(host) {
  let tip = $(".f1-tip", host);
  if (!tip) { tip = el("div", { class: "f1-tip", role: "status", hidden: "" }); host.append(tip); }
  return {
    show(x, y, ...lines) {
      tip.replaceChildren(...lines.filter(Boolean).map((t, i) => el(i ? "span" : "strong", { text: t })));
      tip.hidden = false;
      const box = host.getBoundingClientRect(), w = tip.offsetWidth;
      tip.style.left = `${Math.max(4, Math.min(box.width - w - 4, x - w / 2))}px`;
      tip.style.top = `${y}px`;
    },
    hide() { tip.hidden = true; },
  };
}

// Laps run clockwise or anticlockwise: the sign of the outline's area, with y running down the screen.
function lapDirection(points) {
  let area = 0;
  for (let i = 0; i < points.length - 2; i += 2) {
    const j = (i + 2) % points.length;
    area += points[i] * points[j + 1] - points[j] * points[i + 1];
  }
  return area > 0 ? "Clockwise" : "Anticlockwise";
}

// ------------------------------------------- the track map
// The circuit drawn as the pixel mark draws everything: the lap traced in rounded-square dots on a faint
// dot grid, with a soft shoulder either side. A car — a short comet in the accent colour — runs the lap
// in the racing direction at the stepped ~15fps the rest of the brand moves at, and the start/finish line
// is a chequer across the track. Hovering reads off how far round the lap a point is. An OpenStreetMap
// outline arrives unordered, so it is drawn without the car, the line or the distances.
// map: { points: [x, y, …] in a 1000 box, length (m) } from the start line, or { segments: [[x, y, …]] }.
function trackMap(box, map, { accent = "", label = "" } = {}) {
  const canvas = el("canvas", { class: "trk-canvas", role: "img", "aria-label": label });
  const tip = el("span", { class: "trk-tip", hidden: "" });
  box.replaceChildren(canvas, tip);
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const LAP = 9000, TAIL = 0.055, INK = "#ededed";
  const BODY = [[0, 0], [0.45, 0.45], [-0.45, -0.45], [0.45, -0.45], [-0.45, 0.45]];
  let PITCH = 7, SQ = 5;
  const TAIL_LEVELS = [1, 0.85, 0.7, 0.55, 0.42, 0.32];
  const ordered = Array.isArray(map.points);
  const lines = ordered ? [map.points] : (map.segments || []);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const line of lines) for (let i = 0; i < line.length; i += 2) {
    minX = Math.min(minX, line[i]); maxX = Math.max(maxX, line[i]);
    minY = Math.min(minY, line[i + 1]); maxY = Math.max(maxY, line[i + 1]);
  }
  if (!Number.isFinite(minX) || maxX - minX <= 0) return null;
  let dpr = 1, W = 0, H = 0, cells = [], samples = [], layer = null, ink = null, lit = null, hover = -1, visible = true, looping = false, last = 0;
  const born = performance.now();

  const sprite = (colour) => {
    const c = document.createElement("canvas");
    c.width = c.height = Math.ceil(SQ * dpr);
    const g = c.getContext("2d");
    g.scale(dpr, dpr); g.fillStyle = colour;
    g.beginPath(); g.roundRect(0, 0, SQ, SQ, SQ * 0.28); g.fill();
    return c;
  };

  const layout = () => {
    dpr = devicePixelRatio || 1;
    W = box.clientWidth; H = box.clientHeight;
    if (!W || !H) return false;
    // About 120 dots across whatever the size, so tight sections (Monaco's harbour, Suzuka's esses) stay
    // apart and the curves read as curves rather than steps.
    PITCH = Math.max(3.4, Math.min(4.6, Math.min(W, H * 1.4) / 120)); SQ = PITCH * 0.74;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    const cols = Math.floor(W / PITCH), rows = Math.floor(H / PITCH);
    const x0 = (W - cols * PITCH) / 2, y0 = (H - rows * PITCH) / 2;
    // Fit the outline inside the grid with a margin of a few cells for the shoulder and the S/F label.
    const k = Math.min((cols - 10) * PITCH / (maxX - minX), (rows - 10) * PITCH / (maxY - minY));
    const ox = (W - (maxX - minX) * k) / 2 - minX * k, oy = (H - (maxY - minY) * k) / 2 - minY * k;
    const cellAt = (px, py) => [Math.round((px - x0 - PITCH / 2) / PITCH), Math.round((py - y0 - PITCH / 2) / PITCH)];
    const track = new Map();
    samples = [];
    for (const line of lines) {
      let total = 0;
      for (let i = 2; i < line.length; i += 2) total += Math.hypot(line[i] - line[i - 2], line[i + 1] - line[i - 1]);
      const step = PITCH / 3 / k;
      let run = 0;
      for (let i = 2; i < line.length; i += 2) {
        const ax = line[i - 2], ay = line[i - 1], bx = line[i], by = line[i + 1];
        const seg = Math.hypot(bx - ax, by - ay);
        for (let t = 0; t < seg; t += step) {
          const x = (ax + (bx - ax) * (t / seg)) * k + ox, y = (ay + (by - ay) * (t / seg)) * k + oy;
          const s = ordered ? (run + t) / total : NaN;
          // The road is about two dots wide: every cell within half a pitch either side of the line.
          let key = -1;
          for (const [dx, dy] of BODY) {
            const [c, r] = cellAt(x + dx * PITCH, y + dy * PITCH);
            const k2 = r * cols + c;
            if (key < 0) key = k2;
            if (!track.has(k2)) track.set(k2, { c, r, s });
          }
          samples.push({ x, y, s, key });
        }
        run += seg;
      }
    }
    cells = [...track.values()];
    // The static layer: paper, shoulder, the lap itself and the chequered line, drawn once per layout.
    layer = document.createElement("canvas");
    layer.width = canvas.width; layer.height = canvas.height;
    const g = layer.getContext("2d");
    ink = sprite(INK); lit = sprite(accent || INK);
    const size = ink.width;
    const at = (c, r) => [Math.round((x0 + c * PITCH + (PITCH - SQ) / 2) * dpr), Math.round((y0 + r * PITCH + (PITCH - SQ) / 2) * dpr)];
    const shoulder = new Set();
    for (const { c, r } of cells) for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const key = (r + dr) * cols + c + dc;
      if (!track.has(key)) shoulder.add(key);
    }
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const key = r * cols + c;
      g.globalAlpha = track.has(key) ? 0.56 : shoulder.has(key) ? 0.11 : 0.045;
      g.drawImage(ink, ...at(c, r), size, size);
    }
    if (ordered && samples.length > 8) {
      // The start/finish line: one solid stroke across the track, square-ended, at the first point of the lap.
      const p0 = samples[0], p1 = samples[Math.min(samples.length - 1, 6)];
      const len = Math.hypot(p1.x - p0.x, p1.y - p0.y) || 1;
      const nx = -(p1.y - p0.y) / len, ny = (p1.x - p0.x) / len, reach = PITCH * 2.8;
      g.globalAlpha = 1;
      g.strokeStyle = INK;
      g.lineWidth = Math.max(2, PITCH * 0.55) * dpr;
      g.lineCap = "butt";
      g.beginPath();
      g.moveTo((p0.x - nx * reach) * dpr, (p0.y - ny * reach) * dpr);
      g.lineTo((p0.x + nx * reach) * dpr, (p0.y + ny * reach) * dpr);
      g.stroke();
    }
    canvas._at = at;
    return true;
  };

  const draw = (now) => {
    if (!layer) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.globalAlpha = 1;
    ctx.drawImage(layer, 0, 0);
    const size = lit.width, at = canvas._at;
    if (ordered && !still) {
      // The car: the head at full strength, the tail stepping down through a few ink levels.
      const head = ((now - born) % LAP) / LAP;
      for (const cell of cells) {
        const behind = (head - cell.s + 1) % 1;
        if (behind > TAIL) continue;
        ctx.globalAlpha = TAIL_LEVELS[Math.min(TAIL_LEVELS.length - 1, Math.floor((behind / TAIL) * TAIL_LEVELS.length))];
        ctx.drawImage(lit, ...at(cell.c, cell.r), size, size);
      }
    }
    if (hover >= 0) {
      const cell = cells.find((c) => c.r * Math.floor(W / PITCH) + c.c === hover);
      if (cell) {
        const [x, y] = at(cell.c, cell.r);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = accent || INK; ctx.lineWidth = 1.5 * dpr;
        ctx.beginPath(); ctx.roundRect(x - 2.5 * dpr, y - 2.5 * dpr, size + 5 * dpr, size + 5 * dpr, 3 * dpr); ctx.stroke();
        ctx.drawImage(lit, x, y, size, size);
      }
    }
  };

  if (ordered && map.length) {
    canvas.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      let best = null, bestD = Infinity;
      for (const p of samples) { const d = (p.x - x) ** 2 + (p.y - y) ** 2; if (d < bestD) { bestD = d; best = p; } }
      if (!best || bestD > Math.max(14, PITCH * 3) ** 2) { hover = -1; tip.hidden = true; if (still) draw(0); return; }
      hover = best.key;
      tip.textContent = `${(best.s * map.length / 1000).toFixed(1)} km`;
      tip.style.left = `${best.x}px`; tip.style.top = `${best.y}px`;
      tip.hidden = false;
      if (still) draw(0);
    });
    canvas.addEventListener("pointerleave", () => { hover = -1; tip.hidden = true; if (still) draw(0); });
  }

  const tick = (now) => {
    if (!visible || !box.isConnected) { looping = false; return; }
    if (now - last > 66) { draw(now); last = now; }
    requestAnimationFrame(tick);
  };
  const start = () => { if (!looping && visible) { looping = true; requestAnimationFrame(tick); } };
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (!still) start(); }).observe(box);
  new ResizeObserver(() => { if (layout()) draw(performance.now()); }).observe(box);
  if (layout()) draw(performance.now());
  if (!still) start();
  return { direction: ordered ? lapDirection(map.points) : "" };
}

// An outline the panel didn't have bundled, from OpenStreetMap: SVG path data in a 100 box, unordered.
function osmSegments(path) {
  return path.split("M").filter(Boolean).map((part) => part.trim().split(/\s+/).map(Number)).filter((s) => s.length >= 4);
}

// The track box: sized to the outline's own proportions within sensible limits, then drawn.
function mountTrack(box, map, opts) {
  const ratio = map.w && map.h ? map.w / map.h : 1.4;
  box.style.setProperty("--trk-ratio", String(Math.min(1.9, Math.max(1.05, ratio * 1.08))));
  box.classList.add("trk");
  return trackMap(box, map, opts);
}

// ------------------------------------------- driver and team cards
function f1Ladder(rows) {
  const top = Math.max(...rows.map((r) => Number(r.points) || 0), 1);
  const list = el("ol", { class: "f1-ladder" });
  for (const row of rows) {
    if (row.gap) list.append(el("li", { class: "f1-ladder-gap", "aria-hidden": "true" }, el("span", { text: "···" })));
    const bar = el("span", { class: "f1-ladder-bar" }, el("i"));
    bar.firstChild.style.width = `${Math.max(2, (Number(row.points) / top) * 100)}%`;
    if (row.color) bar.style.setProperty("--c", row.color);
    list.append(el("li", { class: "f1-ladder-row" + (row.me ? " is-me" : "") },
      el("span", { class: "f1-ladder-pos", text: "P" + row.position }),
      el("a", { class: "f1-ladder-name", href: searchHref(row.name), title: row.team || row.name, text: row.code || row.name }),
      bar,
      el("span", { class: "f1-ladder-pts", text: f1Num(row.points) })));
  }
  return list;
}

function f1Standing(card, d) {
  const s = d.standing || {};
  const body = $(".f1-standing-body", card);
  if (!s.position) { card.remove(); return false; }
  const labelText = $("[data-f1-standing-label]", card);
  if (labelText) labelText.textContent = `${d.season} championship`;
  const second = (s.ladder || []).find((r) => r.position === 2);
  const behind = s.position > 1 ? `${f1Num(s.to_leader)} behind the leader` : second ? `Leads by ${f1Num(Number(s.points) - Number(second.points))}` : "";
  const head = el("div", { class: "f1-pos-row" },
    el("span", { class: "f1-pos", text: "P" + s.position }),
    el("div", { class: "f1-pos-meta" },
      el("span", { class: "f1-pos-pts" }, el("strong", { text: f1Num(s.points) }), " pts"),
      el("span", { class: "muted", text: [f1Plural(s.wins, "win"), behind].filter(Boolean).join(" · ") })));
  const parts = [head];
  if (d.role === "team" && d.lineup?.length) {
    const team = el("ul", { class: "f1-lineup" });
    for (const p of d.lineup) {
      team.append(el("li", {},
        el("span", { class: "f1-lineup-num", text: p.number ? "#" + p.number : "" }),
        el("a", { href: searchHref(p.name), text: p.name }),
        el("span", { class: "f1-lineup-pts", text: `P${p.position} · ${f1Num(p.points)} pts` })));
    }
    parts.push(team);
  }
  parts.push(f1Ladder(s.ladder || []));
  body.replaceChildren(...parts);
  return true;
}

// A driver's season, round by round: a column of faint dots per round and one lit dot where they
// finished, P1 at the top. A thin stem runs from the grid slot to the finish, so a recovery drive reads
// as a long stem; retirements drop to their own row. Rounds still to come stay faint.
function driverForm(card, d) {
  const results = d.results || [];
  const body = $(".f1-form-body", card);
  if (!results.length) { card.remove(); return; }
  $("[data-f1-form-label]", card).textContent = `${d.season} season · round by round`;
  const rounds = Math.max(d.rounds || 0, ...results.map((r) => r.round));
  const field = Math.max(20, d.standing?.of || 0, ...results.map((r) => r.position || 0), ...results.map((r) => r.grid || 0));
  const byRound = new Map(results.map((r) => [r.round, r]));
  const wrap = el("div", { class: "f1-chart" });
  const tip = f1Tip(wrap);
  const draw = () => {
    const width = wrap.clientWidth;
    if (!width) return;
    const LEFT = 28, RH = 6.5, TOP = 6, GAP = 12, CW = (width - LEFT) / rounds;
    const height = TOP + field * RH + GAP + RH + 18;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "f1-chart-svg", "aria-hidden": "true" });
    const cx = (round) => LEFT + (round - 0.5) * CW, cy = (pos) => TOP + (pos - 0.5) * RH;
    const dnfY = TOP + field * RH + GAP + RH / 2;
    const dot = (x, y, cls, size = 4.2) => svgNode("rect", { x: x - size / 2, y: y - size / 2, width: size, height: size, rx: size * 0.28, class: cls });
    // Paper: every slot of every round, so the chart is a block of the brand's dots.
    for (let round = 1; round <= rounds; round++) {
      const done = byRound.has(round);
      for (let pos = 1; pos <= field; pos++) svg.append(dot(cx(round), cy(pos), done ? "f1-paper" : "f1-paper is-future", 3));
      svg.append(dot(cx(round), dnfY, done ? "f1-paper" : "f1-paper is-future", 3));
    }
    // The points line: below P10 nobody scores.
    svg.append(svgNode("line", { x1: LEFT - 4, x2: width, y1: TOP + 10 * RH, y2: TOP + 10 * RH, class: "f1-cut" }));
    for (const [pos, text] of [[1, "P1"], [10, "P10"], [field, "P" + field]]) {
      svg.append(svgNode("text", { x: 0, y: cy(pos) + 3, class: "f1-axis" }, text));
    }
    svg.append(svgNode("text", { x: 0, y: dnfY + 3, class: "f1-axis" }, "DNF"));
    const every = CW >= 18 ? 1 : CW >= 10 ? 2 : 5;
    for (let round = 1; round <= rounds; round++) {
      if (round === 1 || round % every === 0 || round === rounds) {
        svg.append(svgNode("text", { x: cx(round), y: height - 3, class: "f1-axis f1-axis--x" + (byRound.has(round) ? "" : " is-future") }, String(round)));
      }
    }
    // The finishing order as one thin line through the season.
    const line = results.filter((r) => r.classified).map((r) => `${cx(r.round)},${cy(r.position)}`);
    if (line.length > 1) svg.append(svgNode("polyline", { points: line.join(" "), class: "f1-trace" }));
    for (const r of results) {
      const x = cx(r.round);
      if (r.grid && r.classified && r.grid !== r.position) {
        svg.append(svgNode("line", { x1: x, x2: x, y1: cy(r.grid), y2: cy(r.position), class: "f1-stem" + (r.grid > r.position ? " is-up" : "") }));
        svg.append(dot(x, cy(r.grid), "f1-grid", 3.4));
      }
      if (!r.classified) { svg.append(dot(x, dnfY, "f1-dnf", 5)); continue; }
      const tier = r.position === 1 ? "is-win" : r.position <= 3 ? "is-podium" : r.points ? "is-points" : "";
      svg.append(dot(x, cy(r.position), "f1-res " + tier, r.position <= 3 ? 5.6 : 4.8));
      if (r.fastest) svg.append(svgNode("circle", { cx: x, cy: cy(r.position), r: 5.2, class: "f1-fl" }));
    }
    // Hover targets, one per round that has been run.
    for (const r of results) {
      const hit = svgNode("rect", { x: cx(r.round) - CW / 2, y: 0, width: CW, height: height - 14, class: "f1-hit" });
      hit.addEventListener("pointerenter", () => {
        const where = r.classified ? `P${r.position}${r.grid ? ` from P${r.grid}` : ""}` : `${r.status}${r.grid ? ` from P${r.grid}` : ""}`;
        tip.show(cx(r.round), Math.max(0, (r.classified ? cy(r.position) : dnfY) - 52), `R${r.round} · ${r.name}`,
          `${where} · ${f1Plural(r.points, "pt")}${r.fastest ? " · fastest lap" : ""}`);
        hit.classList.add("is-on");
      });
      hit.addEventListener("pointerleave", () => { tip.hide(); hit.classList.remove("is-on"); });
      svg.append(hit);
    }
    $(".f1-chart-svg", wrap)?.remove();
    wrap.prepend(svg);
  };
  const f = d.form || {};
  const facts = el("div", { class: "f1-form-facts" });
  const fact = (label, value) => { if (value !== "" && value != null) facts.append(el("span", {}, el("small", { text: label }), el("strong", { text: String(value) }))); };
  if (f.best?.position) fact("Best", `P${f.best.position} · ${f.best.name.replace(/ Grand Prix$/, "")}`);
  fact("Podiums", f.podiums);
  fact("In the points", `${f.points_finishes} of ${f.races}`);
  if (f.average) fact("Average finish", f.average);
  fact("Retirements", f.dnf);
  if (f.fastest) fact("Fastest laps", f.fastest);
  if (f.gained) fact("Places gained", (f.gained > 0 ? "+" : "") + f.gained);
  body.replaceChildren(wrap, facts);
  new ResizeObserver(draw).observe(wrap);
  draw();
}

// A team's season: each round a stack of dots, one dot per few points, the first driver in the team
// colour and the second in a lighter shade of it, so a weekend where both scored is a tall two-tone stack.
function teamForm(card, d) {
  const rounds = d.rounds || [];
  const body = $(".f1-form-body", card);
  if (!rounds.length) { card.remove(); return; }
  $("[data-f1-form-label]", card).textContent = `${d.season} season · points per round`;
  const total = Math.max(d.total_rounds || 0, ...rounds.map((r) => r.round));
  const codes = (d.lineup || []).map((p) => p.code).filter(Boolean).slice(0, 2);
  const most = Math.max(...rounds.map((r) => Number(r.points) || 0), 1);
  const unit = Math.max(1, Math.ceil(most / 16));
  const byRound = new Map(rounds.map((r) => [r.round, r]));
  const wrap = el("div", { class: "f1-chart" });
  const tip = f1Tip(wrap);
  const draw = () => {
    const width = wrap.clientWidth;
    if (!width) return;
    const LEFT = 28, RH = 6.5, ROWS = Math.ceil(most / unit), TOP = 4, CW = (width - LEFT) / total;
    const height = TOP + ROWS * RH + 20;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "f1-chart-svg", "aria-hidden": "true" });
    const cx = (round) => LEFT + (round - 0.5) * CW, cy = (row) => TOP + (ROWS - row + 0.5) * RH;
    const dot = (x, y, cls, size) => svgNode("rect", { x: x - size / 2, y: y - size / 2, width: size, height: size, rx: size * 0.28, class: cls });
    for (const [row, text] of [[ROWS, f1Num(ROWS * unit)], [1, String(unit)]]) svg.append(svgNode("text", { x: 0, y: cy(row) + 3, class: "f1-axis" }, text));
    const every = CW >= 18 ? 1 : CW >= 10 ? 2 : 5;
    for (let round = 1; round <= total; round++) {
      const r = byRound.get(round);
      let row = 1;
      const stack = (points, cls) => { for (let i = 0; i < Math.round(points / unit); i++, row++) svg.append(dot(cx(round), cy(row), cls, 4.6)); };
      if (r) {
        const first = r.cars.filter((c) => c.code === codes[0]).reduce((n, c) => n + Number(c.points), 0);
        const second = r.cars.filter((c) => c.code === codes[1]).reduce((n, c) => n + Number(c.points), 0);
        const other = Number(r.points) - first - second;
        stack(first, "f1-res is-points"); stack(second, "f1-res is-second"); stack(other, "f1-res is-other");
      }
      for (; row <= ROWS; row++) svg.append(dot(cx(round), cy(row), r ? "f1-paper" : "f1-paper is-future", 3));
      if (round === 1 || round % every === 0 || round === total) {
        svg.append(svgNode("text", { x: cx(round), y: height - 3, class: "f1-axis f1-axis--x" + (r ? "" : " is-future") }, String(round)));
      }
    }
    for (const r of rounds) {
      const hit = svgNode("rect", { x: cx(r.round) - CW / 2, y: 0, width: CW, height: height - 14, class: "f1-hit" });
      hit.addEventListener("pointerenter", () => {
        const split = r.cars.map((c) => `${c.code || c.driver} ${c.classified ? "P" + c.position : c.status}`).join(" · ");
        tip.show(cx(r.round), 0, `R${r.round} · ${r.name}`, `${f1Plural(r.points, "pt")} · ${split}`);
        hit.classList.add("is-on");
      });
      hit.addEventListener("pointerleave", () => { tip.hide(); hit.classList.remove("is-on"); });
      svg.append(hit);
    }
    $(".f1-chart-svg", wrap)?.remove();
    wrap.prepend(svg);
  };
  const legend = el("div", { class: "f1-form-facts" });
  codes.forEach((code, i) => legend.append(el("span", { class: "f1-key" + (i ? " is-second" : "") }, el("i"), el("strong", { text: code }))));
  legend.append(el("span", {}, el("small", { text: "One dot" }), el("strong", { text: f1Plural(unit, "pt") })));
  const best = rounds.reduce((a, b) => (Number(b.points) > Number(a.points) ? b : a));
  legend.append(el("span", {}, el("small", { text: "Best round" }), el("strong", { text: `${f1Num(best.points)} · ${best.name.replace(/ Grand Prix$/, "")}` })));
  body.replaceChildren(wrap, legend);
  new ResizeObserver(draw).observe(wrap);
  draw();
}

// The career as one card, laid out by what the numbers mean rather than as six equal tiles: the titles
// on the left, large, with the years they came in; beside them how often a start turned into a win, a
// podium or a pole, as pixel bars against the starts; and along the foot every season from the first
// to the last as a cell, the title years lit, so the shape of a career (when it peaked) reads at a glance.
// A team's results count every car, so it gets its wins and poles per season instead of a share of starts.
function f1Career(box, d) {
  const c = d.career || {};
  if (!c.seasons) { box.remove(); return; }
  const driver = d.role === "driver";
  const titles = c.titles || [];
  const span = c.first === c.last ? String(c.first) : `${c.first}–${c.last}`;
  const head = el("p", { class: "f1-label" }, icon("award"),
    el("span", { text: ["Career", span, f1Plural(c.seasons, "season"), driver && c.starts != null ? f1Plural(c.starts, "start") : ""].filter(Boolean).join(" · ") }));

  const what = driver ? (titles.length === 1 ? "World title" : "World titles")
    : (titles.length === 1 ? "Constructors' title" : "Constructors' titles");
  const crown = el("div", { class: "f1-cv-titles" + (titles.length ? " is-lit" : "") },
    el("span", { class: "f1-cv-big", text: f1Num(titles.length) }),
    el("span", { class: "f1-cv-what", text: what }),
    el("span", { class: "f1-cv-years", text: titles.length ? f1Years(titles) : "Not yet" }));

  // Rates: a bar of 5px dots for each, filled to its share of starts (a team's to its share of the larger).
  const rows = (driver
    ? [["Wins", c.wins], ["Podiums", c.podiums], ["Poles", c.poles]]
    : [["Wins", c.wins], ["Poles", c.poles]]).filter(([, n]) => n != null);
  const scale = driver && c.starts ? c.starts : Math.max(1, ...rows.map(([, n]) => n));
  const rates = el("div", { class: "f1-cv-rates" });
  for (const [label, n] of rows) {
    const bar = el("span", { class: "f1-cv-bar" }, el("i"));
    bar.firstChild.style.width = `${Math.max(1.5, Math.min(100, (n / scale) * 100))}%`;
    const share = driver && c.starts ? `${Math.round((n / c.starts) * 100)}%` : `${(n / c.seasons).toFixed(1)}/yr`;
    rates.append(el("div", { class: "f1-cv-row" },
      el("span", { class: "f1-cv-label", text: label }), bar,
      el("strong", { class: "f1-cv-n", text: f1Num(n) }),
      el("span", { class: "f1-cv-share", text: share, title: driver ? `${share} of starts` : `${share} per season` })));
  }

  // Every season as a cell across the full width: raced, champion, or a year away.
  const raced = new Set(c.years || []), won = new Set(titles);
  const strip = el("div", { class: "f1-cv-seasons" });
  const cells = el("span", { class: "f1-cv-cells" });
  for (let y = c.first; y <= c.last; y++) {
    cells.append(el("i", { class: won.has(y) ? "is-title" : raced.has(y) ? "is-raced" : "",
      title: `${y}${won.has(y) ? " · champion" : raced.has(y) ? "" : " · did not race"}` }));
  }
  strip.append(el("span", { class: "f1-cv-year", text: String(c.first) }), cells, el("span", { class: "f1-cv-year", text: String(c.last) }));
  if (titles.length) strip.append(el("span", { class: "f1-cv-key" }, el("i"), driver ? "Champion" : "Title"));

  box.replaceChildren(head, el("div", { class: "f1-cv-body" }, crown, rates), strip);
}

// ------------------------------------------- the weekend
function f1Countdown(target) {
  const out = el("p", { class: "f1-countdown" });
  const start = new Date(target);
  const tick = () => {
    const ms = start - Date.now();
    if (Number.isNaN(ms)) { out.remove(); return; }
    if (ms <= 0) { out.replaceChildren(el("span", { class: "f1-live-dot" }), "Lights out"); return; }
    const d = Math.floor(ms / 864e5), h = Math.floor((ms % 864e5) / 36e5), m = Math.floor((ms % 36e5) / 6e4);
    out.replaceChildren(el("small", { text: "Race in" }), el("strong", { text: d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m` }));
    if (out.isConnected || !out.dataset.armed) { out.dataset.armed = "1"; setTimeout(tick, 30000); }
  };
  tick();
  return out;
}

function f1Sessions(schedule) {
  const now = Date.now();
  const rows = Object.entries(schedule || {}).filter(([k]) => F1_SESSION_LABEL[k]).sort((a, b) => (a[1] < b[1] ? -1 : 1));
  const next = rows.find(([, at]) => new Date(at) > now);
  const list = el("ol", { class: "f1-sessions" });
  for (const [key, at] of rows) {
    const past = new Date(at) <= now;
    list.append(el("li", { class: (past ? "is-past" : "") + (next && key === next[0] ? " is-next" : "") },
      el("span", { class: "f1-session-name", text: F1_SESSION_LABEL[key] }),
      el("time", { datetime: at, text: `${f1Day(at)} · ${f1Clock(at)}` })));
  }
  return rows.length ? list : null;
}

function f1Calendar(rounds, current) {
  if (!rounds?.length) return null;
  const run = rounds.filter((r) => r.done).length;
  const strip = el("div", { class: "f1-calendar" });
  strip.append(el("span", { class: "f1-calendar-label", text: `Season · ${run} of ${rounds.length} run` }));
  const dots = el("ol", { class: "f1-calendar-dots" });
  for (const r of rounds) {
    dots.append(el("li", { class: (r.done ? "is-done" : "") + (r.round === current ? " is-current" : "") + (r.sprint ? " is-sprint" : ""),
      title: `Round ${r.round} · ${r.name} · ${f1Day(r.date + "T12:00:00Z")}${r.sprint ? " · sprint" : ""}` },
      el("a", { href: searchHref(r.name), "aria-label": r.name })));
  }
  strip.append(dots);
  return strip;
}

// The track box plus what to say under it: length, direction, the year of its first Grand Prix.
function f1TrackBlock(map, accent, label) {
  const box = el("div", { class: "trk" });
  const block = el("div", { class: "f1-trk" }, box);
  if (!map?.points) return null;
  requestAnimationFrame(() => {
    const drawn = mountTrack(box, map, { accent, label });
    const facts = el("p", { class: "f1-trk-facts" });
    const fact = (k, v) => { if (v) facts.append(el("span", {}, el("small", { text: k }), el("strong", { text: v }))); };
    fact("Lap", map.length ? `${(map.length / 1000).toFixed(3)} km` : "");
    fact("Direction", drawn?.direction || "");
    fact("First GP", map.first_gp ? String(map.first_gp) : "");
    block.append(facts);
  });
  return block;
}

// The Grand Prix card's right-hand column, laid out by what each piece of information is: the result as a
// podium of three steps (height = finishing place, colour = team), the race as four numbers, and the
// weekend as one column per day so the order of sessions reads left to right.
function f1Steps(podium) {
  const steps = el("ol", { class: "f1-steps" });
  for (const n of [2, 1, 3]) {
    const p = podium.find((x) => x.position === n);
    if (!p) continue;
    const moved = p.grid && p.grid !== p.position ? p.grid - p.position : 0;
    const li = el("li", { class: `f1-step f1-step--${n}` },
      el("span", { class: "f1-step-pos", text: String(n) }),
      el("a", { class: "f1-step-name", href: searchHref(p.driver), text: p.driver }),
      el("small", { text: p.team }),
      el("span", { class: "f1-step-time", text: p.time || "" }),
      el("span", { class: "f1-step-grid" + (moved > 0 ? " is-up" : moved < 0 ? " is-down" : "") },
        p.grid ? (p.grid === 1 ? "Pole" : `From P${p.grid}`) : "Pit lane", moved ? ` ${moved > 0 ? "▲" : "▼"}${Math.abs(moved)}` : ""));
    if (p.color) li.style.setProperty("--step", p.color);
    steps.append(li);
  }
  return steps;
}

function f1Stats(report) {
  if (!report?.laps) return null;
  const rows = el("dl", { class: "f1-stats" });
  const stat = (k, v, sub) => { if (v) rows.append(el("div", {}, el("dt", { text: k }), el("dd", { text: v }), sub ? el("small", { text: sub }) : null)); };
  stat("Laps", String(report.laps));
  stat("Distance", report.distance ? `${report.distance.toFixed(1)} km` : "");
  stat("Avg speed", report.avg_kph ? `${report.avg_kph.toFixed(1)} km/h` : "");
  if (report.fastest?.time) stat("Fastest lap", report.fastest.time, `${report.fastest.driver}${report.fastest.lap ? ` · lap ${report.fastest.lap}` : ""}`);
  return rows;
}

function f1Rail(schedule, raceDone) {
  const now = Date.now();
  const rows = Object.entries(schedule || {}).filter(([k, v]) => F1_SESSION_LABEL[k] && v).sort((a, b) => (a[1] < b[1] ? -1 : 1));
  if (!rows.length) return null;
  const next = rows.find(([, at]) => new Date(at) > now);
  const days = new Map();
  for (const row of rows) {
    const day = f1Day(row[1]);
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(row);
  }
  const rail = el("div", { class: "f1-rail" });
  rail.style.setProperty("--days", days.size);
  for (const [day, list] of days) {
    const col = el("div", { class: "f1-rail-day" }, el("p", { class: "f1-rail-head", text: day }));
    for (const [key, at] of list) {
      const past = new Date(at) <= now;
      col.append(el("div", { class: "f1-rail-session" + (key === "Race" ? " is-race" : "") + (past ? " is-past" : "") + (next && next[0] === key ? " is-next" : "") },
        el("span", { text: F1_SESSION_LABEL[key] }), el("time", { datetime: at, text: f1Clock(at) })));
    }
    rail.append(col);
  }
  return el("div", { class: "f1-rail-wrap" }, f1Label(raceDone ? "The weekend" : "Weekend schedule", "flag"), rail);
}

function f1Weekend(card, race, { accent = "", title = "Next round", podium = [], report = null, rich = false } = {}) {
  const body = $(".f1-weekend-body", card);
  if (!race || (!race.name && !race.calendar?.length)) { card.remove(); return; }
  card.hidden = false;
  if (!race.name) {   // the season is over: just the strip
    body.replaceChildren(...[el("p", { class: "muted", text: `The ${race.season} season is complete.` }), f1Calendar(race.calendar)].filter(Boolean));
    return;
  }
  const flagImg = race.flag ? img(race.flag, "") : null;
  if (flagImg) flagImg.className = "f1-flag";
  const info = el("div", { class: "f1-weekend-info" },
    el("p", { class: "f1-round" }, el("span", { text: `Round ${race.round}${race.rounds ? ` of ${race.rounds}` : ""}` }),
      race.sprint ? el("span", { class: "f1-sprint" }, icon("bolt"), "Sprint weekend") : null),
    el("h3", { class: "f1-race-name" }, flagImg, el("span", { text: race.name })),
    el("p", { class: "f1-race-where" },
      el("a", { href: searchHref(race.circuit), text: race.circuit }),
      el("span", { text: [race.locality, race.country].filter(Boolean).join(", ") })));
  if (rich) {
    info.append(el("p", { class: "f1-race-date", text: race.done ? `Raced ${f1Day(race.start)}` : `Lights out ${f1Day(race.start)} · ${f1Clock(race.start)}` }));
    if (race.done && podium.length) info.append(...[f1Steps(podium), f1Stats(report)].filter(Boolean));
    else if (!race.done) info.append(f1Countdown(race.start));
    const rail = f1Rail(race.schedule, race.done);
    if (rail) info.append(rail);
  } else if (race.done) {
    info.append(el("p", { class: "f1-race-date", text: `Raced ${f1Day(race.start)}` }));
    if (podium.length) {
      const list = el("ol", { class: "f1-podium" });
      for (const p of podium) {
        const swatch = el("i");
        if (p.color) swatch.style.background = p.color;
        list.append(el("li", {}, el("span", { class: "f1-podium-pos", text: "P" + p.position }), swatch,
          el("a", { href: searchHref(p.driver), text: p.driver }), el("small", { text: p.team })));
      }
      info.append(list);
    }
  } else {
    info.append(f1Countdown(race.start));
    const sessions = f1Sessions(race.schedule);
    if (sessions) info.append(sessions);
  }
  const map = f1TrackBlock(race.map, accent, `${race.circuit}, track map`);
  body.replaceChildren(el("div", { class: "f1-weekend-grid" + (map ? "" : " is-plain") }, map, info));
  const strip = f1Calendar(race.calendar, race.round);
  if (strip) body.append(strip);
  $("[data-f1-gp-label]", card)?.replaceChildren(document.createTextNode(title));
}

function f1Winners(card, winners, held, title = "Recent winners") {
  if (!card || !winners?.length) return;
  card.hidden = false;
  const list = el("ol", { class: "f1-winners-list" });
  // Silverstone also held the 70th Anniversary Grand Prix: name the race when they aren't all the same one.
  const mixed = new Set(winners.map((w) => w.name)).size > 1;
  for (const w of winners) {
    const swatch = el("i");
    if (w.color) swatch.style.background = w.color;
    list.append(el("li", {},
      el("span", { class: "f1-win-year", text: w.season }),
      el("span", { class: "f1-win-driver" }, el("a", { href: searchHref(w.driver), text: w.driver }),
        mixed ? el("small", { text: w.name }) : null),
      el("span", { class: "f1-win-team" }, swatch, w.team),
      el("span", { class: "f1-win-time", text: w.time || "" })));
  }
  // Who owned the place, as one bar split by team over the winners shown (the barcode of a circuit's history).
  const bar = winners.length >= 3 ? el("div", { class: "f1-dominance" }) : null;
  if (bar) {
    const tally = new Map();
    for (const w of winners) { const t = tally.get(w.team) || { n: 0, color: w.color }; t.n++; tally.set(w.team, t); }
    const strip = el("span", { class: "f1-dom-bar", role: "img", "aria-label": "Winning teams" });
    const legend = el("span", { class: "f1-dom-legend" });
    for (const [team, t] of [...tally].sort((a, b) => b[1].n - a[1].n)) {
      const seg = el("i", { title: `${team}: ${t.n} of ${winners.length}` });
      seg.style.flexGrow = t.n;
      if (t.color) seg.style.background = t.color;
      strip.append(seg);
      const dot = el("i");
      if (t.color) dot.style.background = t.color;
      legend.append(el("span", {}, dot, `${team} ${t.n}`));
    }
    bar.append(strip, legend);
  }
  card.replaceChildren(f1Label(held ? `${title} · ${f1Plural(held, "Grand Prix", "Grands Prix")} held` : title, "trophy"), ...(bar ? [bar] : []), list);
}

// The last qualifying session, behind a button: nothing of the grid is drawn until it is asked for.
// It is only offered for the weekend it belongs to; after that the season chart has the story.
function f1Qualifying(panel, quali) {
  const card = $("[data-f1-quali]", panel), body = $("[data-f1-quali-body]", panel);
  if (!card || !body || !quali?.grid?.length) return;
  const at = new Date(quali.session || `${quali.date}T${quali.time || "12:00:00Z"}`);
  const age = Date.now() - at;
  if (Number.isNaN(age) || age < 75 * 60 * 1000 || age > 4 * 864e5) return;
  card.hidden = false;
  const reveal = el("button", { type: "button", class: "f1-reveal" }, icon("eye"), "Show the grid");
  reveal.addEventListener("click", () => {
    const list = el("ol", { class: "f1-quali-grid" });
    for (const row of quali.grid) {
      const swatch = el("i");
      if (row.color) swatch.style.background = row.color;
      list.append(el("li", { class: row.times?.Q3 ? "" : "is-out" },
        el("span", { class: "f1-q-pos", text: "P" + row.position }), swatch,
        el("a", { href: searchHref(row.name), text: row.name }),
        el("small", { text: row.team }),
        el("span", { class: "f1-q-time", text: row.best })));
    }
    body.replaceChildren(list);
  });
  body.replaceChildren(el("div", { class: "f1-quali-head" },
    el("span", { text: `${quali.name} · ${f1Day(at.toISOString())} · results hidden` }), reveal));
}

function f1Plate(panel, d) {
  const plate = $("[data-f1-plate]", panel);
  if (!plate || d.role !== "driver" || !(d.number || d.code)) return;
  plate.replaceChildren(el("span", { class: "f1-plate-num", text: d.number || "" }), el("span", { class: "f1-plate-code", text: d.code || "" }));
  plate.hidden = false;
}

// The team's own logo on a light chip in the picture's corner (logos are drawn for white backgrounds).
// A team whose picture already is its logo doesn't get it twice.
function f1Logo(panel, d) {
  const stage = $(".f1-stage", panel);
  const name = d.role === "driver" ? d.team : d.name;
  if (!stage || !isHttp(d.logo) || stage.classList.contains("is-logo")) return;
  const pic = img(d.logo, `${name} logo`);
  pic.loading = "eager";
  const chip = d.role === "driver"
    ? el("a", { class: "f1-team-logo", href: searchHref(name), title: name }, pic)
    : el("span", { class: "f1-team-logo", title: name }, pic);
  pic.addEventListener("error", () => chip.remove());
  stage.append(chip);
}

function f1Panel(panel) {
  const role = panel.dataset.f1Role;
  getJSON(`/api/f1?title=${encodeURIComponent(panel.dataset.f1Title || "")}`).then((d) => {
    if (!d || !d.role) {
      // Jolpica is down or this isn't someone it knows: the Wikidata half stands on its own.
      for (const sel of ["[data-f1-standing]", "[data-f1-form]", "[data-f1-career]", "[data-f1-next]"]) $(sel, panel)?.remove();
      panel.classList.add("is-offline");
      return;
    }
    if (d.color) panel.style.setProperty("--team", d.color);
    f1Plate(panel, d);
    f1Logo(panel, d);
    // Wikidata's idea of the current seat can lag a transfer; the standings can't.
    const badge = $(".hero-badge", panel);
    if (badge && d.role === "driver" && d.team) {
      const extra = $(".hero-badge-extra", badge) || badge.appendChild(el("span", { class: "hero-badge-extra" }));
      extra.textContent = d.team;
    }
    const live = f1Standing($("[data-f1-standing]", panel), d);
    // A retired driver has no standing: the career totals take its place beside the picture.
    if (!live) { panel.classList.add("is-retired"); $(".f1-top", panel)?.append($("[data-f1-career]", panel)); }
    (role === "team" ? teamForm : driverForm)($("[data-f1-form]", panel), d);
    f1Career($("[data-f1-career]", panel), d);
    // The weekend and the grid belong to whoever is racing this season; a retired driver gets neither.
    const card = $("[data-f1-next]", panel);
    if (!d.active) { card?.remove(); return; }
    if (card) getJSON("/api/f1/next").then((race) => f1Weekend(card, race, { accent: d.color || "" }));
    getJSON("/api/f1/qualifying").then((quali) => f1Qualifying(panel, quali));
  });
}

function gpPanel(panel) {
  getJSON(`/api/f1/gp?title=${encodeURIComponent(panel.dataset.f1Gp)}`).then((gp) => {
    if (!gp?.name) return;
    // The circuit becomes the picture: the generic stage and discipline cards step aside.
    if (gp.map?.points) panel.classList.add("has-gp");
    const lead = gp.podium?.[0]?.color || "";
    if (lead) panel.style.setProperty("--team", lead);
    f1Weekend($("[data-f1-gp-card]", panel), gp, { accent: lead, title: `${gp.season} · round ${gp.round}`, podium: gp.podium || [], report: gp.report, rich: true });
    f1Winners($("[data-f1-winners]", panel), gp.winners, gp.held, "Winners here");
  });
}

function circuitPanel(panel) {
  const stage = $("[data-trk-stage]", panel), facts = $("[data-f1-circuit-facts]", panel);
  const photo = $(".trk-photo", panel);
  const { lat, lon } = panel.dataset;
  const showPhoto = () => { $(".trk-wait", stage)?.remove(); if (photo) photo.hidden = false; else stage.append(el("div", { class: "ms-blank" }, icon("map"), el("span", { text: "No outline of this circuit yet" }))); };
  getJSON(`/api/f1/circuit?title=${encodeURIComponent(panel.dataset.f1Circuit)}&lat=${encodeURIComponent(lat || "")}&lon=${encodeURIComponent(lon || "")}`).then((c) => {
    c = c && !Array.isArray(c) ? c : {};
    let direction = "";
    if (c.map?.points) {
      const box = el("div", { class: "trk trk--stage" });
      stage.replaceChildren(box);
      direction = mountTrack(box, c.map, { label: `${c.name || panel.dataset.f1Circuit}, track map` })?.direction || "";
      stage.classList.add("has-map");
      if (photo) { const side = el("figure", { class: "f1-card trk-photo-card" }, photo); photo.hidden = false; $(".f1-side", panel)?.prepend(side); }
    } else if (lat && lon) {
      getJSON(`/api/f1/track?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&id=${encodeURIComponent(panel.dataset.f1Circuit)}`).then((t) => {
        const segments = t?.path ? osmSegments(t.path) : [];
        if (!segments.length) { showPhoto(); return; }
        const box = el("div", { class: "trk trk--stage" });
        stage.replaceChildren(box);
        mountTrack(box, { segments }, { label: `${panel.dataset.f1Circuit}, outline` });
        stage.classList.add("has-map");
        if (photo) { const side = el("figure", { class: "f1-card trk-photo-card" }, photo); photo.hidden = false; $(".f1-side", panel)?.prepend(side); }
      });
    } else showPhoto();

    // What the circuit is, in a few tiles: the lap, its direction, its Grands Prix.
    const rows = [];
    const row = (k, v, sub) => { if (v) rows.push(el("div", { class: "f1-kv" }, el("small", { text: k }), el("strong", { text: v }), sub ? el("span", { class: "muted", text: sub }) : null)); };
    if (c.map?.length) row("Lap length", `${(c.map.length / 1000).toFixed(3)} km`);
    row("Direction", direction);
    if (c.held) row("Grands Prix", f1Num(c.held), c.map?.first_gp ? `since ${c.map.first_gp}` : "");
    if (c.map?.opened) row("Opened", String(c.map.opened));
    if (c.locality || c.country) row("Where", [c.locality, c.country].filter(Boolean).join(", "));
    if (rows.length) $(".f1-circuit-body", facts).replaceChildren(el("div", { class: "f1-kvs" }, ...rows));
    else facts.remove();

    const raceCard = $("[data-f1-circuit-race]", panel);
    if (c.race?.name && raceCard) {
      raceCard.hidden = false;
      raceCard.replaceChildren(...[f1Label(`${c.race.season} · round ${c.race.round}`, "flag"),
        el("a", { class: "f1-race-name f1-race-name--sm", href: searchHref(c.race.name), text: c.race.name }),
        c.race.done ? el("p", { class: "f1-race-date", text: `Raced ${f1Day(c.race.start)}` }) : f1Countdown(c.race.start),
        c.race.done ? null : f1Sessions(c.race.schedule)].filter(Boolean));
    }
    f1Winners($("[data-f1-winners]", panel), c.winners, c.held);
  });
}

$$("[data-f1]").forEach(f1Panel);
$$("[data-f1-gp]").forEach(gpPanel);
$$("[data-f1-circuit]").forEach(circuitPanel);

// ------------------------------------------------- clubs and leagues
// Club and league panels. The page arrives with skeletons; ESPN's half lands from /api/sport/* and is
// drawn in the house vocabulary with one accent, --club, the club's own colour. Football is drawn as
// football (a table with its zones, W/D/L blocks with scorelines, a match card, the squad on a pitch);
// other sports get the same cards in their terms (win-loss, a conference, the next games).
const spOrdinal = (n) => { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
const spDay = (iso) => f1When(iso, { weekday: "short", day: "numeric", month: "short" });
const spShort = (iso) => f1When(iso, { day: "numeric", month: "short" });
const spSigned = (n) => (Number(n) > 0 ? `+${n}` : String(n ?? ""));

function spLabel(card, text) {
  const hook = $("p > span", card);
  if (hook && text) hook.textContent = text;
}

// A crest, or its first letters on a tile when there is none or it won't load, so a grid never loses a cell.
function spCrest(src, alt, cls = "sp-mini-crest") {
  const blank = () => el("span", { class: cls + " is-blank", text: (alt || "?").slice(0, 3).toUpperCase(), "aria-hidden": "true" });
  if (!isHttp(src)) return blank();
  const pic = el("img", { src, alt: alt || "", class: cls, loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
  pic.addEventListener("error", () => pic.replaceWith(blank()));
  return pic;
}

// A countdown that keeps itself current while it is on the page.
function spCountdown(target, label) {
  const out = el("p", { class: "sp-countdown" });
  const start = new Date(target);
  const tick = () => {
    const ms = start - Date.now();
    if (Number.isNaN(ms) || !out.isConnected && out.dataset.armed) return;
    if (ms <= 0) { out.replaceChildren(el("span", { class: "sp-live-dot" }), "Under way"); return; }
    const d = Math.floor(ms / 864e5), h = Math.floor((ms % 864e5) / 36e5), m = Math.floor((ms % 36e5) / 6e4);
    out.replaceChildren(el("small", { text: label }), el("strong", { text: d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m` }));
    out.dataset.armed = "1";
    setTimeout(tick, 30000);
  };
  tick();
  return out;
}

// The table: a football table whole, with its zones as a stripe down the left; any other league's
// conference around the club. The club's own row is lit.
function spTableRows(rows, { football, compact = false, me = "" } = {}) {
  const head = football ? ["P", "W", "D", "L", "GD", "Pts"] : ["W", "L", "PCT", "GB"];
  const table = el("table", { class: "sp-tbl" + (compact ? " is-compact" : "") });
  table.append(el("thead", {}, el("tr", {}, el("th", { class: "sp-tbl-pos", text: "#" }), el("th", { class: "sp-tbl-team", text: "Team" }),
    ...head.map((h) => el("th", { text: h })))));
  const body = el("tbody");
  for (const r of rows) {
    if (r.gap) body.append(el("tr", { class: "sp-tbl-gap", "aria-hidden": "true" }, el("td", { colspan: String(head.length + 2), text: "···" })));
    const tr = el("tr", { class: (r.me || r.id === me) ? "is-me" : "" });
    if (r.zone?.color) tr.style.setProperty("--zone", r.zone.color);
    if (r.zone?.label) tr.title = r.zone.label;
    const cells = football ? [r.played, r.wins, r.draws, r.losses, spSigned(r.diff), r.points] : [r.wins, r.losses, r.pct, r.behind];
    tr.append(el("td", { class: "sp-tbl-pos", text: String(r.rank || "") }),
      el("td", { class: "sp-tbl-team" }, spCrest(r.logo, r.abbr || r.name), el("a", { href: searchHref(r.name), text: compact ? (r.abbr || r.name) : r.name, title: r.name })),
      ...cells.map((c, i) => el("td", { class: i === cells.length - 1 && football ? "sp-tbl-pts" : "", text: String(c ?? "") })));
    body.append(tr);
  }
  table.append(body);
  return table;
}

function spZoneKey(zones) {
  if (!zones?.length) return null;
  return el("p", { class: "sp-zones" }, ...zones.map((z) => {
    const dot = el("i");
    dot.style.background = z.color;
    return el("span", {}, dot, z.label);
  }));
}

function clubTable(card, d) {
  if (!card) return;
  const rec = d.record || {};
  if (!d.position) { card.remove(); return; }
  spLabel(card, [d.league.name, d.league.group].filter(Boolean).join(" · "));
  const football = d.sport === "soccer";
  const head = el("div", { class: "sp-pos-row" },
    el("span", { class: "sp-pos", text: spOrdinal(d.position) }),
    el("div", { class: "sp-pos-meta" },
      football ? el("span", { class: "sp-pos-pts" }, el("strong", { text: f1Num(rec.points) }), " pts") :
        el("span", { class: "sp-pos-pts" }, el("strong", { text: `${rec.wins}–${rec.losses}` }), rec.pct ? ` · ${rec.pct}` : ""),
      el("span", { class: "muted", text: [football ? `${rec.played} played` : rec.streak && `Streak ${rec.streak}`, `of ${d.of}`].filter(Boolean).join(" · ") })));
  // A whole football table is twenty rows: shown around the club, the rest a click away.
  const rows = d.table || [];
  const around = football ? d.ladder : rows;
  const body = el("div", { class: "sp-table-body" }, head, spTableRows(around, { football, compact: true, me: d.team.id }));
  if (football && rows.length > around.length) {
    const more = el("details", { class: "sp-more" }, el("summary", { text: `Full table · ${rows.length} clubs` }),
      spTableRows(rows, { football, me: d.team.id }), spZoneKey(d.zones));
    body.append(more);
  }
  card.replaceChildren($(".sp-label", card), body);
}

// Form: one block per game, oldest to newest, each with its score and the opponent's crest. A football
// season also gets its totals as a sheet, goals for and against as one split bar.
function clubForm(card, d) {
  if (!card) return;
  const results = d.results || [];
  const rec = d.record || {};
  if (!results.length && !rec.played) { card.remove(); return; }
  const football = d.sport === "soccer";
  spLabel(card, results.length ? `Last ${results.length} · ${football ? "league" : "games"}` : "Season");
  const strip = el("ol", { class: "sp-form-strip" });
  for (const g of results) {
    const block = el("li", { class: `sp-res is-${(g.outcome || "").toLowerCase()}`, title: `${g.home ? "vs" : "at"} ${g.opp} · ${spDay(g.date)}${g.comp ? " · " + g.comp : ""}` },
      el("span", { class: "sp-res-mark", text: g.outcome || "–" }),
      spCrest(g.opp_logo, g.opp_abbr || g.opp),
      el("span", { class: "sp-res-score", text: `${g.for}–${g.against}` }),
      el("span", { class: "sp-res-ha", text: g.home ? "H" : "A" }));
    strip.append(block);
  }
  const parts = [$(".sp-label", card)];
  if (results.length) parts.push(strip); else card.classList.add("is-bare");
  if (rec.played) {
    const sheet = el("dl", { class: "sp-sheet" });
    const cells = football
      ? [["Played", rec.played], ["Won", rec.wins], ["Drawn", rec.draws], ["Lost", rec.losses], ["Goal diff", spSigned(rec.diff)], ["Points", rec.points]]
      : [["Played", rec.played], ["Won", rec.wins], ["Lost", rec.losses], ["Win %", rec.pct], ["Behind", rec.behind], ["Streak", rec.streak]];
    for (const [k, v] of cells) if (v !== "" && v != null) sheet.append(el("div", {}, el("dt", { text: k }), el("dd", { text: String(v) })));
    parts.push(sheet);
    const total = (Number(rec.for) || 0) + (Number(rec.against) || 0);
    if (total) {
      const bar = el("div", { class: "sp-split" }, el("i", { class: "is-for" }), el("i", { class: "is-against" }));
      bar.children[0].style.flexGrow = rec.for;
      bar.children[1].style.flexGrow = rec.against;
      const what = football ? "goals" : "points";
      parts.push(el("div", { class: "sp-split-row" }, el("span", { text: `${rec.for} ${what} for` }), bar, el("span", { text: `${rec.against} against` })));
    }
  }
  card.replaceChildren(...parts);
}

// The next match as a match card: both crests, home on the left, the kick-off in local time with a
// countdown. A game being played now takes its place with the score.
function clubNext(card, d) {
  if (!card) return;
  const g = d.live || (d.fixtures || [])[0];
  if (!g) { card.remove(); return; }
  const football = d.sport === "soccer";
  spLabel(card, d.live ? "Live now" : football ? "Next match" : "Next game");
  const us = { name: d.team.short || d.team.name, logo: d.team.logo, abbr: d.team.abbr, me: true };
  const them = { name: g.opp_short || g.opp, logo: g.opp_logo, abbr: g.opp_abbr };
  const [home, away] = g.home ? [us, them] : [them, us];
  const side = (t) => el("a", { class: "sp-vs-side" + (t.me ? " is-me" : ""), href: searchHref(t.me ? d.team.name : g.opp) },
    spCrest(t.logo, t.abbr || t.name, "sp-vs-crest"), el("span", { class: "sp-vs-name", text: t.name }));
  const middle = d.live
    ? el("div", { class: "sp-vs-mid is-live" }, el("strong", { text: g.home ? `${g.for}–${g.against}` : `${g.against}–${g.for}` }), el("small", {}, el("span", { class: "sp-live-dot" }), g.detail || "Live"))
    : el("div", { class: "sp-vs-mid" }, el("strong", { text: f1Clock(g.date) }), el("small", { text: spDay(g.date) }));
  const match = el("div", { class: "sp-vs" }, side(home), middle, side(away));
  const meta = el("p", { class: "sp-vs-meta" }, ...[g.comp, g.venue, g.tv && `On ${g.tv}`].filter(Boolean).map((t) => el("span", { text: t })));
  const parts = [$(".sp-label", card), match, meta];
  if (!d.live) parts.push(spCountdown(g.date, football ? "Kick-off in" : "Starts in"));
  const later = (d.fixtures || []).slice(d.live ? 0 : 1, 4);
  if (later.length) {
    parts.push(el("ol", { class: "sp-fixtures" }, ...later.map((f) => el("li", {},
      el("span", { class: "sp-fx-day", text: spShort(f.date) }),
      el("span", { class: "sp-fx-ha", text: f.home ? "vs" : "at" }),
      spCrest(f.opp_logo, f.opp_abbr || f.opp),
      el("a", { href: searchHref(f.opp), text: f.opp }),
      el("span", { class: "sp-fx-comp", text: f.comp || "" })))));
  }
  card.replaceChildren(...parts);
}

// Football: the squad on a pitch, forwards at the top, one row per line, each player a shirt number and a
// name. Other sports: the roster by position.
function clubSquad(card, d) {
  if (!card) return;
  const groups = d.squad || [];
  if (!groups.length) { card.remove(); return; }
  const count = groups.reduce((n, g) => n + g.players.length, 0);
  spLabel(card, `Squad · ${count} players${d.coach ? ` · ${d.sport === "soccer" ? "manager" : "coach"} ${d.coach}` : ""}`);
  const person = (p) => {
    const chip = el("a", { class: "sp-player", href: searchHref(p.name), title: [p.position, p.nat, p.age && `${p.age} years`].filter(Boolean).join(" · ") },
      el("span", { class: "sp-shirt", text: p.number || "–" }),
      el("span", { class: "sp-player-name", text: p.short || p.name }));
    const flag = img(p.flag, p.nat);
    if (flag) { flag.className = "sp-player-flag"; chip.append(flag); }
    return chip;
  };
  if (d.sport === "soccer") {
    const pitch = el("div", { class: "sp-squad-pitch" });
    for (const g of [...groups].reverse()) {
      pitch.append(el("div", { class: `sp-line is-${g.code.toLowerCase()}` },
        el("span", { class: "sp-line-label", text: g.label }),
        el("div", { class: "sp-line-row" }, ...g.players.map(person))));
    }
    card.replaceChildren($(".sp-label", card), pitch);
  } else {
    card.replaceChildren($(".sp-label", card), el("div", { class: "sp-roster" }, ...groups.map((g) =>
      el("div", { class: "sp-roster-group" }, el("p", { class: "sp-roster-label", text: g.label }), el("div", { class: "sp-line-row" }, ...g.players.map(person))))));
  }
}

// A panel whose live half didn't come (or never had one): the picture beside the facts and the About,
// one compact block, instead of a lone picture over full-width rows with a single fact in them.
function compactPanel(panel) {
  const stage = $(".sp-stage, .val-stage, .es-stage", panel);
  if (!stage || $(".sp-compact", panel)) return;
  const anchor = stage.parentElement === panel ? stage : stage.parentElement;
  const wrap = el("div", { class: "sp-compact" });
  const side = el("div", { class: "sp-compact-side" });
  anchor.before(wrap);
  for (const node of $$(":scope > .ms-chips, :scope > [data-specs], :scope > details.about", panel)) side.append(node);
  wrap.append(stage, side);
  if (anchor !== stage && !$(".sp-card, .val-card", anchor)) anchor.remove();
  panel.classList.add("is-compact");  // the bento's ResizeObserver re-fits it to the narrower column
}

function clubPanel(panel) {
  const q = new URLSearchParams({ title: panel.dataset.clubTitle || "", league: panel.dataset.clubLeague || "",
    sport: panel.dataset.clubSport || "", country: panel.dataset.clubCountry || "" });
  getJSON(`/api/sport/club?${q}`).then((d) => {
    if (!d || !d.team) {
      for (const sel of ["[data-sp-table]", "[data-sp-form]", "[data-sp-next]", "[data-sp-squad]"]) $(sel, panel)?.remove();
      panel.classList.add("is-offline");
      compactPanel(panel);
      return;
    }
    if (d.team.color) panel.style.setProperty("--club", d.team.color);
    if (d.team.alt) panel.style.setProperty("--club-2", d.team.alt);
    const plate = $("[data-sp-plate]", panel);
    if (plate && d.position) {
      plate.replaceChildren(el("span", { class: "sp-plate-pos", text: spOrdinal(d.position) }), el("span", { class: "sp-plate-code", text: d.team.abbr || "" }));
      plate.hidden = false;
    }
    clubTable($("[data-sp-table]", panel), d);
    clubForm($("[data-sp-form]", panel), d);
    clubNext($("[data-sp-next]", panel), d);
    clubSquad($("[data-sp-squad]", panel), d);
    if (!$("[data-sp-table]", panel) && !$("[data-sp-form]", panel)) panel.classList.add("is-offline");
  });
}

function leaguePanel(panel) {
  getJSON(`/api/sport/league?qid=${encodeURIComponent(panel.dataset.league)}`).then((d) => {
    const tableCard = $("[data-lg-table]", panel), roundCard = $("[data-lg-round]", panel);
    if (!d || !d.tables) { tableCard?.remove(); roundCard?.remove(); return; }
    const football = d.sport === "soccer";
    spLabel(tableCard, football ? "Table" : "Standings");
    const parts = [$(".sp-label", tableCard)];
    for (const t of d.tables) {
      if (d.tables.length > 1) parts.push(el("p", { class: "sp-group", text: t.name }));
      parts.push(spTableRows(t.rows, { football }));
    }
    const key = spZoneKey(d.zones);
    if (key) parts.push(key);
    tableCard.replaceChildren(...parts);
    if (!d.games?.length) { roundCard.remove(); return; }
    const live = d.games.some((g) => g.state === "in");
    spLabel(roundCard, live ? "Live and this round" : "This round");
    const list = el("div", { class: "sp-round-list" });
    let day = "";
    for (const g of d.games) {
      const today = spDay(g.date);
      if (today !== day) { day = today; list.append(el("p", { class: "sp-round-day", text: today })); }
      const side = (t, right) => el("a", { class: "sp-rg-side" + (right ? " is-right" : "") + (g.state === "post" && !t.winner && football ? " is-quiet" : ""), href: searchHref(t.name) },
        spCrest(t.logo, t.abbr || t.name), el("span", { text: t.name }));
      const mid = g.state === "pre"
        ? el("span", { class: "sp-rg-mid", text: f1Clock(g.date) })
        : el("span", { class: "sp-rg-mid is-score" + (g.state === "in" ? " is-live" : "") }, `${g.home.score ?? ""}–${g.away.score ?? ""}`,
          el("small", { text: g.state === "in" ? g.detail : "FT" }));
      list.append(el("div", { class: "sp-rg" + (g.state === "in" ? " is-live" : "") }, side(g.home, false), mid, side(g.away, true)));
    }
    roundCard.replaceChildren($(".sp-label", roundCard), list);
  });
}

$$("[data-club]").forEach(clubPanel);
$$("[data-league]").forEach(leaguePanel);

// ------------------------------------------------- Valorant
// Valorant teams and players from VLR.gg. The game's look rather than the house one: cut corners, the
// red, agents by role. Every card was drawn as a skeleton of its size; each one fills or steps aside.
const VAL_ROLES = { duelist: "Duelist", initiator: "Initiator", controller: "Controller", sentinel: "Sentinel", flex: "Flex" };

function valAgent(a, size = "") {
  const pic = img(a.icon, a.name);
  const box = el("span", { class: `val-agent is-${a.role}${size ? " is-" + size : ""}`, title: `${a.name} · ${VAL_ROLES[a.role] || ""}` });
  if (pic) box.append(pic); else box.textContent = a.name.slice(0, 2);
  return box;
}

function valComp(agents) {
  return el("span", { class: "val-comp" }, ...(agents || []).map((a) => valAgent(a, "sm")));
}

function valCardDone(card, ...parts) {
  card.replaceChildren($(".val-label", card), ...parts.filter(Boolean));
}

function valPlate(panel, text, flag) {
  const plate = $("[data-val-plate]", panel);
  if (!plate || !text) return;
  const f = img(flag, "");
  plate.replaceChildren(...[f, el("span", { text })].filter(Boolean));
  plate.hidden = false;
}

const VAL_PLACE = (rank) => (rank === 1 ? "is-gold" : rank === 2 ? "is-silver" : rank === 3 ? "is-bronze" : "");

// Placements: a tally on top (titles, podiums, winnings), then the events by year, newest first, each a
// row with the finish as a medal for the top three and the prize to the right. Six show; the rest fold.
function valTrophies(card, d) {
  if (!card) return;
  const all = d.placements || [];
  if (!all.length) { card.remove(); return; }
  const titles = all.filter((p) => p.rank === 1).length;
  const podiums = all.filter((p) => p.rank && p.rank <= 3).length;
  const tally = el("div", { class: "val-tally" },
    ...[["Titles", titles], ["Podiums", podiums], ["Events", all.length]].map(([k, v]) => el("span", {}, el("b", { text: String(v) }), el("small", { text: k }))),
    d.winnings ? el("span", { class: "is-money" }, el("b", { text: d.winnings }), el("small", { text: "Winnings" })) : null);
  const row = (p) => el("li", { class: "val-place " + VAL_PLACE(p.rank) },
    el("span", { class: "val-medal", text: p.place || "—" }),
    el("span", { class: "val-place-what" }, el("a", { href: p.url, text: p.event }), el("small", { text: p.stage })),
    el("span", { class: "val-place-prize", text: p.prize || "" }));
  const byYear = (list) => {
    const out = [];
    let year = "";
    for (const p of list) {
      if (p.year !== year) { year = p.year; out.push(el("li", { class: "val-place-year", text: year || "—" })); }
      out.push(row(p));
    }
    return el("ol", { class: "val-places" }, ...out);
  };
  const parts = [tally, byYear(all.slice(0, 6))];
  if (all.length > 6) parts.push(el("details", { class: "sp-more" }, el("summary", { text: `${all.length - 6} more events` }), byYear(all.slice(6))));
  valCardDone(card, ...parts);
}

// One series: who, the series score, then a row per map with its score and both compositions.
function valSeries(s, open) {
  const won = s.outcome === "W";
  const row = el("details", { class: "val-series-row " + (won ? "is-win" : s.outcome === "L" ? "is-loss" : "") });
  if (open) row.open = true;
  row.append(el("summary", {},
    el("span", { class: "val-series-date", text: s.date ? spShort(s.date) : "" }),
    el("span", { class: "val-series-event" }, el("strong", { text: s.event }), el("small", { text: s.stage })),
    el("span", { class: "val-series-score" }, el("b", { text: String(s.us ?? "–") }), ":", el("b", { text: String(s.them ?? "–") })),
    el("span", { class: "val-series-opp" }, spCrest(s.opp_logo, s.opp_tag || s.opp, "val-crest"), el("span", { text: s.opp }))));
  if (s.maps?.length) {
    row.append(el("div", { class: "val-maps-played" }, ...s.maps.map((m) => el("div", { class: "val-map-line " + (m.won ? "is-win" : "is-loss") },
      valComp(m.agents),
      el("span", { class: "val-map-score" }, el("b", { text: String(m.us) }), el("span", { class: "val-map-name", text: m.map }), el("b", { text: String(m.them) })),
      valComp(m.against)))));
  }
  return row;
}

// Rating over this roster's matches: a line on the house dot paper, a win lit, a loss hollow.
function valHistory(card, d) {
  if (!card) return;
  const pts = d.history || [];
  if (pts.length < 3) { card.remove(); return; }
  const W = 520, H = 170, pad = 26;
  const ratings = pts.map((p) => p.rating);
  const lo = Math.min(...ratings) - 15, hi = Math.max(...ratings) + 15;
  const x = (i) => pad + (i / (pts.length - 1)) * (W - pad * 2);
  const y = (r) => H - pad - ((r - lo) / (hi - lo || 1)) * (H - pad * 2);
  const svg = svgNode("svg", { viewBox: `0 0 ${W} ${H}`, class: "val-hist-svg", role: "img", "aria-label": `Rating from ${ratings[0]} to ${ratings[ratings.length - 1]}` });
  for (const r of [lo + 15, (lo + hi) / 2, hi - 15]) {
    svg.append(svgNode("line", { x1: pad, x2: W - pad, y1: y(r), y2: y(r), class: "val-hist-grid" }),
      svgNode("text", { x: 2, y: y(r) + 3, class: "val-hist-axis" }, document.createTextNode(String(Math.round(r)))));
  }
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.rating).toFixed(1)}`).join("");
  svg.append(svgNode("path", { d: `${line}L${x(pts.length - 1)},${H - pad}L${x(0)},${H - pad}Z`, class: "val-hist-area" }),
    svgNode("path", { d: line, class: "val-hist-line" }));
  const tip = f1Tip(card);
  pts.forEach((p, i) => {
    const dot = svgNode("rect", { x: x(i) - 2.5, y: y(p.rating) - 2.5, width: 5, height: 5, class: "val-hist-dot " + (p.won ? "is-win" : "is-loss") });
    const hit = svgNode("rect", { x: x(i) - 5, y: 0, width: 10, height: H, class: "val-hist-hit" });
    hit.addEventListener("mouseenter", () => {
      const box = card.getBoundingClientRect(), r = svg.getBoundingClientRect();
      tip.show(r.left - box.left + (x(i) / W) * r.width, r.top - box.top + (y(p.rating) / H) * r.height - 58,
        `${p.won ? "Win" : "Loss"} vs ${p.opp}`, `${p.rating} (${spSigned(p.change)})`, `${p.event} · ${p.date}`);
    });
    hit.addEventListener("mouseleave", () => tip.hide());
    svg.append(dot, hit);
  });
  const first = pts[0], last = pts[pts.length - 1];
  const wins = pts.filter((p) => p.won).length;
  valCardDone(card, svg, el("p", { class: "val-hist-foot" },
    el("span", { text: `${first.date} → ${last.date}` }),
    el("span", { text: `${wins}–${pts.length - wins} over ${pts.length} matches` }),
    el("strong", { text: spSigned(last.rating - first.rating) })));
}

// The map pool: a strip per map with its splash behind, the win rate as a bar, and attack against
// defence round rates as a split.
function valMaps(card, d) {
  if (!card) return;
  const maps = (d.maps || []).filter((m) => m.played);
  if (!maps.length) { card.remove(); return; }
  const list = el("ol", { class: "val-pool" });
  for (const m of maps.slice(0, 8)) {
    const bg = isHttp(m.image) ? el("span", { class: "val-pool-bg", "aria-hidden": "true" }) : null;
    if (bg) bg.style.backgroundImage = `url("${m.image}")`;
    const bar = el("span", { class: "val-bar" }, el("i"));
    bar.firstChild.style.width = `${m.win}%`;
    const split = el("span", { class: "val-split", title: `Attack ${m.atk}% · Defence ${m.def}% of rounds won` }, el("i", { class: "is-atk" }), el("i", { class: "is-def" }));
    split.children[0].style.flexGrow = m.atk || 1;
    split.children[1].style.flexGrow = m.def || 1;
    list.append(el("li", { class: "val-pool-row" + (m.win >= 60 ? " is-strong" : m.win < 45 ? " is-weak" : "") }, bg,
      el("span", { class: "val-pool-name" }, el("strong", { text: m.map }), el("small", { text: `${m.played} maps · ${m.w}W ${m.l}L` })),
      el("span", { class: "val-pool-win" }, el("b", { text: `${m.win}%` }), bar),
      el("span", { class: "val-pool-sides" }, el("small", { text: `ATK ${m.atk}%` }), split, el("small", { text: `DEF ${m.def}%` }))));
  }
  valCardDone(card, list);
}

function valTeam(panel, d) {
  valPlate(panel, d.tag, d.flag);
  const rank = $("[data-val-rank]", panel);
  if (rank) {
    const record = d.record?.w != null ? d.record : null;
    const share = record ? Math.round((100 * record.w) / Math.max(1, record.w + record.l)) : 0;
    const bar = el("span", { class: "val-bar is-wide" }, el("i"));
    bar.firstChild.style.width = `${share}%`;
    valCardDone(rank,
      el("div", { class: "val-rank-row" },
        el("span", { class: "val-rank-num" + (/^\d+$/.test(d.rank) ? "" : " is-text"), text: /^\d+$/.test(d.rank) ? `#${d.rank}` : (d.rank || "—") }),
        el("span", { class: "val-rank-meta" }, el("strong", { text: d.region || (d.rank === "Unranked" ? "Not ranked now" : "") }),
          d.rating ? el("small", { text: `Rating ${d.rating}` }) : null)),
      record ? el("div", { class: "val-record" }, el("span", { text: `${record.w}W` }), bar, el("span", { text: `${record.l}L` })) : null,
      record ? el("p", { class: "val-rank-foot", text: `${share}% of matches won with this roster` }) : null,
      d.winnings ? el("p", { class: "val-winnings" }, el("small", { text: "Total winnings" }), el("strong", { text: d.winnings })) : null);
  }
  const roster = $("[data-val-roster]", panel);
  if (roster) {
    if (!d.players?.length) roster.remove();
    else {
      const five = el("ol", { class: "val-five" }, ...d.players.map((p) => {
        const pic = img(p.photo, p.alias);
        const face = el("span", { class: "val-face" }, pic || el("span", { class: "val-face-initial", text: p.alias.slice(0, 1).toUpperCase() }));
        const flag = img(p.flag, "");
        if (flag) flag.className = "val-flag";
        return el("li", { class: "val-player" + (p.role ? " is-" + p.role.toLowerCase().replace(/\W+/g, "-") : "") },
          el("a", { href: searchHref(p.alias + " valorant"), title: p.name },
            face,
            el("span", { class: "val-player-alias" }, flag, el("strong", { text: p.alias }), p.captain ? el("span", { class: "val-capt", title: "Captain", text: "C" }) : null),
            el("small", { text: p.role ? p.role : p.name })));
      }));
      const staff = d.staff?.length ? el("p", { class: "val-staff" }, ...d.staff.map((s) => el("span", {}, el("small", { text: s.role }), s.alias))) : null;
      valCardDone(roster, five, staff);
    }
  }
  const series = $("[data-val-series]", panel);
  if (series) {
    const items = [...(d.upcoming || []), ...(d.results || [])];
    if (!items.length) series.remove();
    else valCardDone(series, el("div", { class: "val-series" }, ...items.slice(0, 6).map((s, i) => valSeries(s, i < 2))));
  }
  valMaps($("[data-val-maps]", panel), d);
  valHistory($("[data-val-history]", panel), d);
  if (!$("[data-val-maps]", panel) && !$("[data-val-history]", panel)) $(".val-duo", panel)?.remove();
}

function valPlayer(panel, d) {
  valPlate(panel, d.alias, d.flag);
  const career = $("[data-val-career]", panel);
  const c = d.career || {};
  if (career) {
    if (!c.rounds) career.remove();
    else {
      const split = el("div", { class: "val-roles" }, ...c.roles.map((r) => {
        const seg = el("i", { class: "is-" + r.role, title: `${VAL_ROLES[r.role]} ${r.share}%` });
        seg.style.flexGrow = r.share;
        return seg;
      }));
      valCardDone(career,
        el("div", { class: "val-rank-row" }, el("span", { class: "val-rank-num", text: c.rating?.toFixed ? c.rating.toFixed(2) : String(c.rating) }),
          el("span", { class: "val-rank-meta" }, el("strong", { text: "Rating" }), el("small", { text: `${f1Num(c.maps)} maps · ${f1Num(c.rounds)} rounds` }))),
        el("dl", { class: "val-stats" }, ...[["ACS", c.acs], ["K:D", c.kd], ["ADR", c.adr], ["KAST", c.kast != null ? `${c.kast}%` : ""], ["FK:FD", c.fkfd], ["Kills", f1Num(c.kills)]]
          .filter(([, v]) => v !== "" && v != null).map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: String(v) })))),
        split,
        el("p", { class: "val-roles-key" }, ...c.roles.map((r) => el("span", { class: "is-" + r.role }, el("i"), `${VAL_ROLES[r.role]} ${r.share}%`))));
    }
  }
  const team = $("[data-val-team]", panel);
  if (team) {
    const now = d.team;
    const rows = [];
    if (now) rows.push(el("a", { class: "val-team-now", href: searchHref(now.name + " valorant") }, spCrest(now.logo, now.name, "val-crest is-big"),
      el("span", {}, el("strong", { text: now.name }), el("small", { text: now.note || "Current team" }))));
    if (d.past?.length) {
      rows.push(el("p", { class: "val-sub", text: "Before" }), el("ol", { class: "val-past" }, ...d.past.map((t) => el("li", {},
        spCrest(t.logo, t.name, "val-crest"), el("a", { href: searchHref(t.name + " valorant"), text: t.name }), el("small", { text: [t.tag, t.note].filter(Boolean).join(" · ") })))));
    }
    if (!rows.length) team.remove(); else valCardDone(team, ...rows);
  }
  const agents = $("[data-val-agents]", panel);
  if (agents) {
    if (!d.agents?.length) agents.remove();
    else {
      const top = Math.max(...d.agents.map((a) => a.use || 0), 1);
      valCardDone(agents, el("ol", { class: "val-pool-agents" }, ...d.agents.slice(0, 8).map((a) => {
        const bar = el("span", { class: "val-bar" }, el("i"));
        bar.firstChild.style.width = `${(100 * a.use) / top}%`;
        return el("li", { class: `val-agent-card is-${a.role}` },
          valAgent(a, "lg"),
          el("span", { class: "val-agent-name" }, el("strong", { text: a.name }), el("small", { text: `${VAL_ROLES[a.role]} · ${a.maps} maps` })),
          el("span", { class: "val-agent-use" }, el("b", { text: `${a.use}%` }), bar),
          el("dl", { class: "val-agent-line" }, ...[["R", a.rating], ["ACS", a.acs != null ? Math.round(a.acs) : ""], ["K:D", a.kd], ["ADR", a.adr != null ? Math.round(a.adr) : ""], ["KAST", a.kast != null ? `${a.kast}%` : ""]]
            .map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: String(v ?? "–") })))));
      })));
    }
  }
  const log = $("[data-val-log]", panel);
  if (log) {
    const games = (d.results || []).flatMap((s) => (s.maps || []).map((m) => ({ ...m, s })));
    if (!games.length) log.remove();
    else {
      const best = Math.max(2, ...games.map((g) => g.rating || 0));
      valCardDone(log, el("ol", { class: "val-log-list" }, ...games.slice(0, 12).map((g) => {
        const bar = el("span", { class: "val-bar" + (g.rating >= 1 ? " is-good" : "") }, el("i"));
        bar.firstChild.style.width = `${Math.min(100, (100 * (g.rating || 0)) / best)}%`;
        return el("li", { class: "val-log-row " + (g.won ? "is-win" : "is-loss") },
          ...(g.agents || []).slice(0, 1).map((a) => valAgent(a)),
          el("span", { class: "val-log-what" }, el("strong", { text: g.map }), el("small", { text: `vs ${g.s.opp} · ${g.s.event}` })),
          el("span", { class: "val-map-score" }, el("b", { text: String(g.us) }), "–", el("b", { text: String(g.them) })),
          el("span", { class: "val-log-rating" }, el("b", { text: g.rating != null ? g.rating.toFixed(2) : "–" }), bar),
          el("span", { class: "val-log-kda", text: g.k != null ? `${g.k}/${g.d}/${g.a}` : "" }));
      })));
    }
  }
}

function valPanel(panel) {
  const role = panel.dataset.valRole;
  getJSON(`/api/esports?${new URLSearchParams({ title: panel.dataset.valTitle || "", role, game: "valorant" })}`).then((d) => {
    if (!d || !d.role) {
      $$(".val-card", panel).forEach((c) => c.remove());
      $(".val-duo", panel)?.remove();
      panel.classList.add("is-offline");
      compactPanel(panel);
      return;
    }
    const src = $("[data-val-source]", panel);
    if (src && isHttp(d.source)) src.href = d.source;
    (d.role === "team" ? valTeam : valPlayer)(panel, d);
    valTrophies($("[data-val-trophies]", panel), d);
  });
}

$$("[data-val]").forEach(valPanel);

// ------------------------------------------------- VCT seasons
// "vct 2026": the season's events behind a closed picker that names the one on show, and that event drawn
// as a result. No photograph of the trophy is free to use, so it is drawn: the Champions trophy's
// faceted, blade-cut cup on a tall stem and a stepped base, in a muted gold.
function vctTrophy() {
  return svgNode("svg", { viewBox: "0 0 64 96", class: "vct-trophy", "aria-hidden": "true" },
    svgNode("path", { d: "M14 6h36l-4 20-14 16-14-16z", class: "vct-trophy-cup" }),
    svgNode("path", { d: "M14 6l18 22L50 6M32 28v14", class: "vct-trophy-facet" }),
    svgNode("path", { d: "M24 12l8 9 8-9", class: "vct-trophy-mark" }),
    svgNode("path", { d: "M29 42h6l2 26h-10z", class: "vct-trophy-stem" }),
    svgNode("path", { d: "M22 68h20l3 8H19z", class: "vct-trophy-base" }),
    svgNode("path", { d: "M15 76h34v12H15z", class: "vct-trophy-base" }),
    svgNode("path", { d: "M15 82h34", class: "vct-trophy-facet" }));
}

// Champions has one trophy, the same every year: Riot's studio photograph, cut out of its background
// (static/vct-champions-trophy.png, kept on this computer). Masters and the rest get the drawn one, as does
// Champions if the picture can't load.
function vctChampionsTrophy() {
  const pic = el("img", { src: "/static/vct-champions-trophy.png", alt: "The Valorant Champions trophy", class: "vct-trophy-photo", decoding: "async" });
  pic.addEventListener("error", () => pic.replaceWith(vctTrophy()));
  return pic;
}

const VCT_STATUS = { ongoing: "Live", upcoming: "Upcoming", completed: "Completed" };

// A team's mark: its logo; if that won't load (or there is none), its flag; failing that, its initials
// with the country beside the name doing the rest.
function vctTeamMark(team, cls) {
  const initials = () => el("span", { class: cls + " is-blank", text: (team.name || team.team || "?").slice(0, 3).toUpperCase(), "aria-hidden": "true" });
  const flag = () => {
    if (!isHttp(team.flag)) return initials();
    const f = el("img", { src: team.flag, alt: "", class: cls + " is-flag", referrerpolicy: "no-referrer" });
    f.addEventListener("error", () => f.replaceWith(initials()));
    return f;
  };
  if (!isHttp(team.logo)) return flag();
  const pic = el("img", { src: team.logo, alt: team.name || team.team || "", class: cls, loading: "lazy", referrerpolicy: "no-referrer" });
  pic.addEventListener("error", () => pic.replaceWith(flag()));
  return pic;
}

function vctPicker(card, season, onPick) {
  const body = $("[data-vct-groups]", card);
  body.replaceChildren(...season.groups.map((g) => el("div", { class: "vct-group" },
    el("span", { class: "vct-group-name", text: g.name }),
    el("div", { class: "vct-chips" }, ...g.events.map((e) => {
      const chip = el("button", { type: "button", class: `vct-chip is-${e.status}`, "data-id": e.id, "data-group": g.name,
        title: [e.name, e.dates, e.prize].filter(Boolean).join(" · ") }, e.short || e.name,
        e.status !== "completed" ? el("small", { text: VCT_STATUS[e.status] || "" }) : null);
      chip.addEventListener("click", () => { card.open = false; onPick(e.id); });
      return chip;
    })))));
}

// The summary names the event on show, as the season calls it: "Champions", "Masters · London".
function vctMark(card, id) {
  let shown = null;
  for (const chip of $$(".vct-chip", card)) {
    const on = chip.dataset.id === id;
    chip.setAttribute("aria-pressed", String(on));
    if (on) shown = chip;
  }
  const now = $("[data-vct-now]", card);
  if (!now || !shown) return;
  const group = shown.dataset.group, short = shown.firstChild.textContent;
  now.replaceChildren(...[el("strong", { text: group }), group === short ? null : el("span", { text: short })].filter(Boolean));
}

function vctMatch(m) {
  const side = (t, key) => el("span", { class: "vct-m-side" + (m.winner === key ? " is-won" : m.winner ? " is-lost" : "") },
    img(t.flag, "") || el("i", { class: "vct-noflag" }), el("a", { href: searchHref(t.name + " valorant"), text: t.name }),
    el("b", { text: t.score ?? "–" }));
  return el("li", { class: "vct-m" + (m.final ? " is-final" : "") },
    el("span", { class: "vct-m-stage", text: m.stage.replace(/–/g, " · ") }),
    el("a", { class: "vct-m-teams", href: m.url }, side(m.a, "a"), side(m.b, "b")),
    m.eta ? el("span", { class: "vct-m-eta", text: m.eta }) : null);
}

function vctEvent(body, ev) {
  if (!ev || !ev.name) {
    body.replaceChildren(el("div", { class: "val-card vct-empty", text: "VLR.gg didn't answer for this event. Try again in a minute." }));
    return;
  }
  const status = ev.done ? "completed" : ev.live ? "ongoing" : "upcoming";
  const logo = img(ev.logo, "");
  if (logo) logo.className = "vct-ev-logo";
  const flag = img(ev.flag, "");
  if (flag) flag.className = "vct-flag";
  const head = el("div", { class: "vct-ev-head" }, logo,
    el("div", { class: "vct-ev-title" },
      el("strong", {}, el("a", { href: ev.source, text: ev.name })),
      el("span", { class: "vct-ev-meta" }, ...[ev.dates, ev.prize && `${ev.prize} prize pool`].filter(Boolean).map((t) => el("span", { text: t })),
        ev.location ? el("span", {}, flag, ev.location) : null)),
    el("span", { class: `vct-status is-${status}`, text: VCT_STATUS[status] }));
  const parts = [head];
  const st = ev.standings || [];
  const named = (name) => st.find((s) => s.team.toLowerCase() === (name || "").toLowerCase()) || {};

  if (ev.champion) {
    const c = ev.champion;
    const f = ev.final;
    const won = f ? (f.winner === "a" ? f.a : f.b) : null;
    const lost = f ? (f.winner === "a" ? f.b : f.a) : null;
    const finalist = (t, row, champion) => el("a", { class: "vct-finalist" + (champion ? " is-champion" : ""), href: searchHref(t.name + " valorant") },
      vctTeamMark({ name: t.name, logo: t.logo || row.logo, flag: t.flag || row.flag }, "vct-finalist-crest"),
      el("span", { class: "vct-finalist-name", text: t.name }),
      el("small", { text: [t.country || row.country, champion ? c.prize : row.prize].filter(Boolean).join(" · ") }));
    const title = ev.kind === "champions" ? "World champions" : ev.kind === "masters" ? "Masters champions" : "Champions";
    parts.push(el("div", { class: "vct-champ" + (f ? "" : " is-solo") },
      el("div", { class: "vct-champ-cup" }, ev.kind === "champions" ? vctChampionsTrophy() : vctTrophy(), el("small", { text: title })),
      f && won && lost
        ? el("div", { class: "vct-final" },
          finalist(won, named(won.name), true),
          el("a", { class: "vct-final-score", href: f.url, title: "Grand final" },
            el("small", { text: "Grand final" }), el("span", {}, el("b", { text: String(won.score) }), "–", el("b", { text: String(lost.score) }))),
          finalist(lost, named(lost.name), false))
        : el("div", { class: "vct-final" }, finalist({ name: c.team, logo: c.logo, flag: c.flag, country: c.country }, c, true))));
    const podium = st.filter((s) => s.rank >= 3 && s.rank <= 4).slice(0, 2);
    if (podium.length) {
      parts.push(el("ol", { class: "vct-podium" }, ...podium.map((s) => el("li", { class: `vct-step is-${s.rank}` },
        el("span", { class: "vct-step-place", text: s.place }), vctTeamMark(s, "vct-step-crest"),
        el("a", { href: searchHref(s.team + " valorant"), text: s.team }), el("small", { text: [s.country, s.prize].filter(Boolean).join(" · ") })))));
    }
  }
  const rest = ev.champion ? st.filter((s) => s.rank > 4) : st;
  if (rest.length) {
    parts.push(el("div", { class: "val-card vct-table" }, el("p", { class: "val-label", text: ev.champion ? "Rest of the standings" : "Standings" }),
      el("ol", { class: "vct-rows" }, ...rest.map((s) => el("li", {},
        el("span", { class: "vct-row-place", text: s.place }), vctTeamMark(s, "val-crest"),
        el("a", { href: searchHref(s.team + " valorant"), text: s.team }),
        el("span", { class: "vct-row-country", text: s.country }), el("span", { class: "vct-row-prize", text: s.prize }))))));
  }
  const lists = [];
  if (ev.upcoming?.length) lists.push(el("div", { class: "val-card" }, el("p", { class: "val-label", text: "Next matches" }), el("ol", { class: "vct-ms" }, ...ev.upcoming.map(vctMatch))));
  if (ev.results?.length) lists.push(el("div", { class: "val-card" }, el("p", { class: "val-label", text: "Latest results" }), el("ol", { class: "vct-ms" }, ...ev.results.map(vctMatch))));
  if (lists.length) parts.push(el("div", { class: "vct-lists" }, ...lists));
  if (parts.length === 1) parts.push(el("div", { class: "val-card vct-empty", text: "Nothing has been played yet. Teams, standings and results appear here once it starts." }));
  body.replaceChildren(...parts);
}

function vctPanel(panel) {
  const body = $("[data-vct-body]", panel), picker = $("[data-vct-picker]", panel);
  const q = new URLSearchParams({ year: panel.dataset.vctYear, event: panel.dataset.vctEvent || "", region: panel.dataset.vctRegion || "" });
  const open = (id) => {
    vctMark(picker, id);
    body.classList.add("is-loading");
    getJSON(`/api/vct/event?id=${encodeURIComponent(id)}`).then((ev) => { body.classList.remove("is-loading"); vctEvent(body, ev); });
  };
  getJSON(`/api/vct/season?${q}`).then((season) => {
    if (!season?.groups?.length) {
      picker.remove();
      body.replaceChildren(el("div", { class: "val-card vct-empty", text: `VLR.gg has no ${panel.dataset.vctYear} season to show, or didn't answer.` }));
      return;
    }
    vctPicker(picker, season, open);
    if (season.pick) open(season.pick);
  });
}

$$("[data-vct]").forEach(vctPanel);

// ------------------------------------------------- esports in other games (Liquipedia)
function esPanel(panel) {
  const q = new URLSearchParams({ title: panel.dataset.esTitle || "", role: panel.dataset.esRole || "team", game: panel.dataset.esGame || "" });
  getJSON(`/api/esports?${q}`).then((d) => {
    const info = $("[data-es-info]", panel), list = $("[data-es-list]", panel);
    if (!d || !d.role) { info?.remove(); list?.remove(); panel.classList.add("is-offline"); compactPanel(panel); return; }
    const src = $("[data-es-source]", panel);
    if (src && isHttp(d.source)) src.href = d.source;
    if (info) {
      const flag = img(d.flag, d.country || d.location || "");
      if (flag) flag.className = "es-flag";
      const head = d.role === "player"
        ? el("div", { class: "es-id" }, el("strong", { text: d.alias }), el("span", {}, flag, d.name || ""), d.position ? el("span", { class: "es-role", text: d.position }) : null)
        : el("div", { class: "es-id" }, el("strong", { text: d.league || d.region || d.title }), el("span", {}, flag, [d.city, d.location].filter(Boolean).join(", ")),
          d.created?.length ? el("span", { class: "es-role", text: `Since ${d.created[d.created.length - 1].slice(0, 4)}` }) : null);
      const facts = el("dl", { class: "es-facts" }, ...(d.facts || []).map((f) => el("div", {}, el("dt", { text: f.label }),
        el("dd", {}, f.label === "Team" ? el("a", { href: searchHref(f.value), text: f.value }) : f.value))));
      info.replaceChildren($(".sp-label", info), head, facts);
    }
    if (list) {
      if (d.role === "player") {
        if (!d.titles?.length) { list.remove(); return; }
        spLabel(list, `World titles · ${d.titles.length}`);
        list.replaceChildren($(".sp-label", list), el("ol", { class: "es-titles" }, ...d.titles.map((t) => el("li", {},
          el("span", { class: "es-cup", "aria-hidden": "true" }, icon("trophy")), el("strong", { text: t.year }), el("span", { text: t.event })))));
      } else {
        if (!d.roster?.length) { list.remove(); return; }
        list.replaceChildren($(".sp-label", list), el("ol", { class: "es-roster" }, ...d.roster.map((p) => {
          const flag = img(p.flag, p.country);
          if (flag) flag.className = "es-flag";
          return el("li", {},
            el("span", { class: "es-pos", text: p.position || "" }),
            el("a", { class: "es-alias", href: searchHref(p.alias + " " + (d.game?.name || "")) }, flag, el("strong", { text: p.alias })),
            el("span", { class: "es-name", text: p.name }),
            el("small", { text: p.joined ? `since ${p.joined.slice(0, 7)}` : "" }));
        })));
      }
    }
  });
}

$$("[data-es]").forEach(esPanel);
$$("[data-compact]").forEach(compactPanel);

for (const shot of $$(".sp-crest, .val-shot, .es-shot")) {
  shot.addEventListener("error", () => {
    if (shot.dataset.fallback && shot.src !== shot.dataset.fallback) shot.src = shot.dataset.fallback;
  });
}

// The stage shows its picture whole rather than cropped, so nothing needs re-fitting when it lands; a
// dead original falls back to the thumbnail Wikipedia also offered.
for (const shot of $$(".ms-shot, .f1-shot")) {
  shot.addEventListener("error", () => {
    if (shot.dataset.fallback && shot.src !== shot.dataset.fallback) shot.src = shot.dataset.fallback;
  });
}

// Motorsport bento: every cell gets the same width, so the grid reads as one block rather than as rows
// of different sizes. The column count is chosen to leave the fewest empty cells in the last row while
// keeping a cell near the width it should occupy; a short last row is fine, a ragged one is not.
function layoutSpecs(grid) {
  const cells = [...grid.children];
  if (!cells.length) return;
  const fit = () => {
    const width = grid.clientWidth;
    if (!width) return;
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 10;
    const ideal = width / 220;                      // how many cells would sit at the right width
    let cols = cells.length, score = Infinity;
    for (let c = 2; c <= Math.min(7, cells.length); c++) {
      const rows = Math.ceil(cells.length / c);
      const waste = rows * c - cells.length;
      // Straying from the width a cell should have costs more than an empty slot, but a last row
      // holding a single cell is the one shape that really reads as broken.
      const s = Math.abs(c - ideal) * 2 + waste * 1.5 + (rows > 1 && cells.length % c === 1 ? 4 : 0);
      if (s < score) { score = s; cols = c; }
    }
    // Half a pixel spare, so rounding never wraps the last cell onto a line of its own.
    cells.forEach((cell) => cell.style.setProperty("--cell-w", `calc((100% - ${(cols - 1) * gap}px) / ${cols} - 0.5px)`));
    grid.classList.add("is-laid");
  };
  new ResizeObserver(fit).observe(grid);
  fit();
}
$$("[data-specs]").forEach((grid) => layoutSpecs(grid));

// Bento rows: as many columns as fit (tiles at least `min` wide, at most six), then the tiles shared out
// evenly across the rows, shorter rows first, each row's tiles splitting its width. No row is left short.
function balanceBento(grid, min = 160) {
  const tiles = [...grid.children];
  if (tiles.length < 2) return;
  const fit = () => {
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    const cols = Math.max(1, Math.min(6, tiles.length, Math.floor((grid.clientWidth + gap) / (min + gap))));
    const rows = Math.ceil(tiles.length / cols);
    const per = Math.floor(tiles.length / rows), extra = tiles.length % rows;
    let i = 0;
    for (let r = 0; r < rows; r++) {
      const n = per + (r >= rows - extra ? 1 : 0);  // shorter rows first: their wider tiles lead
      // Half a pixel spare per row, so rounding never wraps the last tile onto a line of its own.
      for (let k = 0; k < n; k++) tiles[i++].style.setProperty("--bento-w", `calc((100% - ${(n - 1) * gap}px) / ${n} - 0.5px)`);
    }
    grid.classList.add("is-balanced");
  };
  new ResizeObserver(fit).observe(grid);
  fit();
}
$$(".gm-tiles").forEach((grid) => balanceBento(grid));

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

// ------------------------------------------ content-aware crop focus
// When a picture is much taller (or wider) than its box, object-fit: cover keeps only a slice.
// Pick the slice with the most going on: edges and colours that differ from the picture's own
// border (its background), plus skin tones for photos of people. Needs a CORS-readable copy;
// hosts that don't allow one keep the CSS default position.
function focusCrop(pic, { faces = false } = {}) {
  if (!pic) return;
  // Runs once the picture has both loaded and been laid out: it may arrive from cache before it's
  // in the page, or sit in a collapsed grid row until "Show all".
  let done = false;
  const observer = "ResizeObserver" in window ? new ResizeObserver(() => run()) : null;
  const run = () => {
    if (done || !pic.naturalWidth || !pic.naturalHeight) return;
    const box = pic.getBoundingClientRect();
    if (!box.width || !box.height) return;
    done = true;
    observer?.disconnect();
    const imageRatio = pic.naturalWidth / pic.naturalHeight, boxRatio = box.width / box.height;
    if (Math.abs(imageRatio - boxRatio) / boxRatio < 0.08) return;   // nearly fits: nothing to choose
    const axis = imageRatio < boxRatio ? "y" : "x";
    const visible = axis === "y" ? imageRatio / boxRatio : boxRatio / imageRatio;
    const probe = new Image();
    probe.crossOrigin = "anonymous";
    probe.referrerPolicy = "no-referrer";
    probe.onload = () => {
      const at = focalPoint(probe, axis, visible, faces);
      if (at != null) pic.style.objectPosition = axis === "y" ? `50% ${at}%` : `${at}% 50%`;
    };
    probe.src = pic.currentSrc || pic.src;
  };
  observer?.observe(pic);
  pic.addEventListener("load", run, { once: true });
  run();
}

// Percentage for object-position along one axis, or null when the picture can't be read or has
// no clear subject.
function focalPoint(image, axis, visible, faces) {
  const scale = 64 / Math.max(image.naturalWidth, image.naturalHeight);
  const w = Math.max(4, Math.round(image.naturalWidth * scale)), h = Math.max(4, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let px;
  try { ctx.drawImage(image, 0, 0, w, h); px = ctx.getImageData(0, 0, w, h).data; } catch { return null; }
  const at = (x, y) => (y * w + x) * 4;
  // Background: the average colour around the edge.
  let br = 0, bg = 0, bb = 0, n = 0;
  for (let x = 0; x < w; x++) for (const y of [0, h - 1]) { const i = at(x, y); br += px[i]; bg += px[i + 1]; bb += px[i + 2]; n++; }
  for (let y = 0; y < h; y++) for (const x of [0, w - 1]) { const i = at(x, y); br += px[i]; bg += px[i + 1]; bb += px[i + 2]; n++; }
  br /= n; bg /= n; bb /= n;
  const lum = (i) => 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
  const size = axis === "y" ? h : w;
  const profile = new Float64Array(size);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = at(x, y), r = px[i], g = px[i + 1], b = px[i + 2];
      if (px[i + 3] < 32) continue;   // transparent
      const edge = (x + 1 < w ? Math.abs(lum(i) - lum(at(x + 1, y))) : 0) + (y + 1 < h ? Math.abs(lum(i) - lum(at(x, y + 1))) : 0);
      const apart = Math.hypot(r - br, g - bg, b - bb) / 4;
      let weight = edge + apart;
      if (faces && r > 95 && g > 40 && b > 20 && r > g && r > b && r - Math.min(g, b) > 15 && Math.abs(r - g) > 15) weight *= 2.5;
      // People: heads are near the top of the subject, so lean upward a little.
      if (faces && axis === "y") weight *= 1.3 - 0.6 * (y / h);
      profile[axis === "y" ? y : x] += weight;
    }
  }
  const span = Math.max(1, Math.round(size * visible));
  if (span >= size) return null;
  let sum = 0;
  for (let i = 0; i < span; i++) sum += profile[i];
  let best = sum, start = 0, worst = sum;
  for (let i = span; i < size; i++) {
    sum += profile[i] - profile[i - span];
    if (sum > best) { best = sum; start = i - span + 1; }
    worst = Math.min(worst, sum);
  }
  if (!best || (best - worst) / best < 0.08) return null;   // evenly busy: keep the default
  return Math.round((start / (size - span)) * 100);
}

// ------------------------------------------------- entity hero: collage
for (const collage of $$("[data-collage-q]")) {
  const main = $(".collage-main", collage);
  const mainImage = $("img", main);
  if (mainImage && !collage.classList.contains("is-logo") && !main.classList.contains("collage-flag")) focusCrop(mainImage, { faces: !collage.closest(".is-poster") });
  const recoverMain = async () => {
    if (mainImage?.dataset.fallback && mainImage.src !== mainImage.dataset.fallback) {
      const fallback = mainImage.dataset.fallback;
      mainImage.dataset.fallback = "";
      const probe = new Image();
      probe.referrerPolicy = "no-referrer";
      if (await new Promise((resolve) => { probe.onload = () => resolve(true); probe.onerror = () => resolve(false); probe.src = fallback; })) {
        mainImage.src = fallback;
        focusCrop(mainImage);
        return;
      }
    }
    const items = await strip("images", collage.dataset.collageQ);
    const pic = await firstLoading(items.filter((i) => isHttp(i.thumb) && isHttp(i.url)));
    if (pic) {
      const picture = img(pic.thumb, pic.title);
      main.replaceWith(el("a", { class: "collage-main", href: pic.url, title: pic.title }, picture));
      focusCrop(picture, { faces: true });
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
    side.replaceChildren(...picked.map((p) => {
      const picture = img(p.thumb, p.title);
      focusCrop(picture, { faces: true });
      return el("a", { class: "collage-slot", href: p.url, title: p.title }, picture);
    }));
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
    let top = await firstLoading(items.filter((i) => isHttp(i.thumb)));
    if (!top) {
      // No engine pictures: the lead story's own share image still makes a proper card.
      const thumb = await leadImage(items[0].url);
      top = thumb ? await firstLoading([{ ...items[0], thumb }]) : null;
    }
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

// ---------------------------------- screen sections: filmography + cast
// Both grids show one row, however many columns fit, until "Show all" opens the rest.
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const searchHref = (q) => `/search?q=${encodeURIComponent(q)}`;

function clampGrid(section, grid, rows = 1) {
  const more = $("[data-screen-more]", section);
  let open = false;
  const apply = () => {
    const cards = [...grid.children];
    const cols = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(" ").length);
    const limit = cols * rows;
    cards.forEach((c, i) => { c.hidden = !open && i >= limit; });
    more.hidden = cards.length <= limit;
    $("span", more).textContent = open ? "Show less" : `Show all ${cards.length}`;
    more.classList.toggle("is-open", open);
    more.setAttribute("aria-expanded", String(open));
  };
  more.onclick = () => {
    open = !open;
    apply();
    if (!open) section.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  grid._clampApply = apply;
  if (!grid._clampObserver && "ResizeObserver" in window) {
    let lastWidth = 0;
    grid._clampObserver = new ResizeObserver(([entry]) => {
      if (Math.abs(entry.contentRect.width - lastWidth) > 1) { lastWidth = entry.contentRect.width; grid._clampApply(); }
    });
    grid._clampObserver.observe(grid);
  }
  apply();
}

function personCard(p, sub) {
  const photo = el("span", { class: "person-photo" }, el("span", { class: "person-initials", text: initials(p.name) }));
  const pic = img(p.photo, "");
  if (pic) { photo.append(pic); pic.addEventListener("load", () => photo.classList.add("has-photo")); focusCrop(pic, { faces: true }); }
  return el("a", { class: "person-card", href: searchHref(p.q || p.name), title: p.name },
    photo,
    el("span", { class: "person-name", text: p.name }),
    sub ? el("span", { class: "person-role", text: sub }) : null);
}

for (const section of $$("[data-cast-qid]")) {
  const { castQid, castMedia, castTmdb } = section.dataset;
  const grid = $("[data-people]", section), crewRow = $("[data-crew]", section);
  getJSON(`/api/cast?qid=${encodeURIComponent(castQid)}&media=${encodeURIComponent(castMedia)}&tmdb=${encodeURIComponent(castTmdb)}`).then((data) => {
    const cast = Array.isArray(data?.cast) ? data.cast.filter((p) => p && p.name) : [];
    const crew = Array.isArray(data?.crew) ? data.crew.filter((p) => p && p.name) : [];
    if (!cast.length && !crew.length) {
      // Keep the server-rendered names if there are any; otherwise there's nothing to show.
      if (!$("a.person-card", grid)) return section.remove();
      for (const photo of $$(".person-photo.skeleton", grid)) {
        photo.classList.remove("skeleton");
        photo.append(el("span", { class: "person-initials", text: initials(photo.nextElementSibling?.textContent || "") }));
      }
      return clampGrid(section, grid);
    }
    if (crew.length) {
      crewRow.replaceChildren(...crew.map((c) => {
        const face = el("span", { class: "crew-photo" }, el("span", { class: "person-initials", text: initials(c.name) }));
        const pic = img(c.photo, "");
        if (pic) { face.append(pic); focusCrop(pic, { faces: true }); }
        return el("a", { class: "crew-chip", href: searchHref(c.q || c.name) }, face,
          el("span", { class: "crew-text" }, el("span", { class: "crew-job", text: c.job }), el("span", { class: "crew-name", text: c.name })));
      }));
      crewRow.hidden = false;
    }
    if (cast.length) {
      grid.replaceChildren(...cast.map((p) =>
        personCard(p, [p.role, p.episodes > 1 ? `${p.episodes} episodes` : ""].filter(Boolean).join(" · "))));
      clampGrid(section, grid);
    } else {
      grid.remove();
    }
    $("[data-screen-count]", section).textContent = cast.length ? String(cast.length) : "";
    $("[data-screen-credit]", section).textContent = data.source === "tmdb"
      ? "Credits and photos from TMDB · this product uses the TMDB API but is not endorsed or certified by TMDB"
      : "Credits from Wikidata · photos from Wikipedia";
  });
}

function posterCard(w) {
  const art = el("span", { class: "poster-art is-empty" }, el("span", { class: "poster-fallback", text: w.title }));
  const pic = img(w.poster, "");
  if (pic) { art.append(pic); pic.addEventListener("load", () => art.classList.remove("is-empty")); focusCrop(pic); }
  if (w.upcoming) art.append(el("span", { class: "poster-flag", text: "Upcoming" }));
  const kind = w.media === "tv" ? (w.episodes > 1 ? `TV · ${w.episodes} ep` : "TV") : "Movie";
  return el("a", { class: "poster-card", href: searchHref(w.q || w.title), title: w.title + (w.year ? ` (${w.year})` : "") },
    art,
    el("span", { class: "poster-title", text: w.title }),
    el("span", { class: "poster-meta", text: [w.year, kind].filter(Boolean).join(" · ") }),
    w.role ? el("span", { class: "poster-role", text: w.role }) : null);
}

for (const section of $$("[data-films-qid]")) {
  const { filmsQid, filmsTmdb } = section.dataset;
  const grid = $("[data-films-grid]", section), controls = $("[data-films-controls]", section);
  getJSON(`/api/filmography?qid=${encodeURIComponent(filmsQid)}&tmdb=${encodeURIComponent(filmsTmdb)}`).then((data) => {
    const items = Array.isArray(data?.items) ? data.items.filter((w) => w && w.title) : [];
    if (!items.length) return section.remove();
    const state = { filter: "all", sort: "popular" };
    const counts = { all: items.length, movie: items.filter((w) => w.media === "movie").length, tv: items.filter((w) => w.media === "tv").length };
    const render = () => {
      const list = items.filter((w) => state.filter === "all" || w.media === state.filter);
      // "Best known": most-voted (TMDB) or most-linked across Wikipedias first, newer breaking ties.
      // Undated titles (often unreleased, sometimes just missing data) go last rather than first.
      if (state.sort === "newest") list.sort((a, b) => (b.date || "0000").localeCompare(a.date || "0000"));
      else list.sort((a, b) => (b.pop || 0) - (a.pop || 0) || (b.date || "").localeCompare(a.date || ""));
      grid.replaceChildren(...list.map(posterCard));
      $("[data-screen-count]", section).textContent = String(list.length);
      clampGrid(section, grid);
    };
    const choose = (attr, btn) => $$(`[${attr}]`, controls).forEach((b) => {
      b.classList.toggle("is-active", b === btn);
      b.setAttribute("aria-pressed", String(b === btn));
    });
    for (const btn of $$("[data-films-filter]", controls)) {
      const n = counts[btn.dataset.filmsFilter];
      if (!n) { btn.hidden = true; continue; }
      btn.append(el("span", { class: "seg-count", text: String(n) }));
      btn.addEventListener("click", () => { state.filter = btn.dataset.filmsFilter; choose("data-films-filter", btn); render(); });
    }
    // Only films (or only series): the filter has nothing to choose between.
    if (!counts.movie || !counts.tv) $(".seg", controls).hidden = true;
    for (const btn of $$("[data-films-sort]", controls)) {
      btn.addEventListener("click", () => { state.sort = btn.dataset.filmsSort; choose("data-films-sort", btn); render(); });
    }
    controls.hidden = false;
    $("[data-screen-credit]", section).textContent = data.source === "tmdb"
      ? "Credits and posters from TMDB · this product uses the TMDB API but is not endorsed or certified by TMDB"
      : "Credits from Wikidata · posters from Wikipedia";
    render();
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
// On a phone the plot scrolls sideways: open it on the searched model, not the cheap or old end.
function centreAmPlot(panel) {
  const wrap = $(".am-plot-wrap", panel), self = $(".am-mark--self .am-dot", panel);
  if (!wrap || !self || wrap.scrollWidth <= wrap.clientWidth) return;
  const dot = self.getBoundingClientRect(), box = wrap.getBoundingClientRect();
  wrap.scrollLeft += dot.left + dot.width / 2 - (box.left + box.width / 2);
}
// The best-value line joins only the models shown, so hiding one leaves no bend behind.
function drawAmFrontier(svg) {
  const line = $(".am-frontier", svg);
  if (!line) return;
  // SVG y grows downward: walking right (pricier), keep each point that scores higher (smaller y).
  const pts = $$("[data-mark]:not(.is-off) .am-dot", svg).map((c) => [+c.getAttribute("cx"), +c.getAttribute("cy")])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let best = Infinity;
  const kept = pts.filter(([, y]) => (y < best ? ((best = y), true) : false));
  line.setAttribute("points", kept.map((p) => p.join(",")).join(" "));
  line.style.display = kept.length > 1 ? "" : "none";
}
// The Models menu: a checked box is on the chart; its table row dims when it's off. The button counts what's shown.
function setAmToggle(box, on) {
  const panel = box.closest("[role=tabpanel]");
  const key = box.dataset.amToggle;
  box.checked = on;
  $(`[data-mark="${key}"]`, panel)?.classList.toggle("is-off", !on);
  $(`.am-row[data-key="${key}"]`, panel)?.classList.toggle("is-off", !on);
}
function syncAmFilter(panel) {
  const boxes = $$("[data-am-toggle]", panel);
  const count = $("[data-am-count]", panel);
  if (count) count.textContent = `${boxes.filter((b) => b.checked).length} of ${boxes.length}`;
  const svg = $("[data-am-plot]", panel);
  placeAmLabels(svg);
  drawAmFrontier(svg);
}
for (const box of $$("[data-am-toggle]")) {
  box.addEventListener("change", () => { setAmToggle(box, box.checked); syncAmFilter(box.closest("[role=tabpanel]")); });
}
// "Show all" brings every model back; "Only …" keeps the searched model and hides the rest.
for (const button of $$("[data-am-show]")) {
  button.addEventListener("click", () => {
    const panel = button.closest("[role=tabpanel]");
    for (const box of $$("[data-am-toggle]", panel)) {
      setAmToggle(box, button.dataset.amShow === "all" || box.closest(".am-pick--self") !== null);
    }
    syncAmFilter(panel);
  });
}
// The menu closes on a click elsewhere or Escape, like any dropdown.
document.addEventListener("click", (event) => {
  for (const menu of $$("[data-am-filter][open]")) if (!menu.contains(event.target)) menu.open = false;
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const menu of $$("[data-am-filter][open]")) { menu.open = false; $("summary", menu).focus(); }
});

// ------------------------------------------- AI model chart: sortable model table
for (const table of $$("[data-am-table]")) {
  const body = $("tbody", table);
  for (const head of $$("[data-am-sort]", table)) {
    head.addEventListener("click", () => {
      const th = head.closest("th"), key = head.dataset.amSort;
      // First click: the natural order for the column (best score, cheapest, newest, A–Z); again to flip.
      const natural = key === "score" || key === "released" ? "descending" : "ascending";
      const dir = th.getAttribute("aria-sort") === natural ? (natural === "ascending" ? "descending" : "ascending") : natural;
      for (const other of $$("th[aria-sort]", table)) other.setAttribute("aria-sort", "none");
      th.setAttribute("aria-sort", dir);
      const value = (row) => (key === "name" || key === "released" ? row.dataset[key] : row.dataset[key] === "" ? NaN : +row.dataset[key]);
      const rows = $$("tr", body).sort((a, b) => {
        const x = value(a), y = value(b);
        if (typeof x === "number" && (Number.isNaN(x) || Number.isNaN(y))) return Number.isNaN(x) - Number.isNaN(y); // unpriced last
        const order = typeof x === "string" ? x.localeCompare(y) : x - y;
        return dir === "ascending" ? order : -order;
      });
      body.append(...rows);
    });
  }
}

// ------------------------------------------- AI model card: prices in the home currency
// Prices arrive in US dollars (data-money) and show in the home currency when a rate is known.
// aimodels.money is the server-side twin of fmtMoney.
for (const card of $$("[data-am-card]")) {
  const current = card.dataset.fxCode ? { code: card.dataset.fxCode, symbol: card.dataset.fxSymbol, rate: +card.dataset.fxRate }
    : { code: "USD", symbol: "$", rate: 1 };
  const fmtMoney = (usd, cur) => {
    const v = usd * cur.rate;
    const d = v > 0 && v < 0.1 ? 4 : v < 100 ? 2 : 0;
    let text = new Intl.NumberFormat(cur.code === "INR" ? "en-IN" : "en-US", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
    if (text.endsWith(".00") && v >= 10) text = text.slice(0, -3);
    return cur.symbol + text;
  };
  const tickLabel = (v, cur) => cur.symbol + (v < 1 ? v.toFixed(2) : new Intl.NumberFormat(cur.code === "INR" ? "en-IN" : "en-US").format(v));

  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs, text) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text) node.textContent = text;
    return node;
  };
  // Price axes are log scales in dollars: place round numbers of the shown currency on them.
  const relabelAxis = (svg) => {
    const lx0 = +svg.dataset.lx0, lx1 = +svg.dataset.lx1, left = +svg.dataset.left, right = +svg.dataset.right, bottom = +svg.dataset.bottom;
    const group = $("[data-am-xticks]", svg);
    group.replaceChildren();
    for (let e = -2; e <= 7; e++) {
      for (const m of [1, 3]) {
        const v = m * 10 ** e, lx = Math.log10(v / current.rate);
        if (lx <= lx0 || lx >= lx1) continue;
        const x = (left + (lx - lx0) / (lx1 - lx0) * (right - left)).toFixed(1);
        group.append(svgEl("line", { class: "am-grid", x1: x, x2: x, y1: bottom, y2: bottom + 5 }),
          svgEl("text", { class: "am-tick", x, y: bottom + 21, "text-anchor": "middle" }, tickLabel(v, current)));
      }
    }
    const label = $("[data-am-xlabel]", svg);
    if (label) label.textContent = `API price, ${current.symbol.trim()} per million tokens`;
  };
  for (const node of $$("[data-money]", card)) {
    if (!node.hasAttribute("data-money-alt")) node.textContent = fmtMoney(+node.dataset.money, current) + (node.dataset.suffix || "");
  }
  for (const svg of $$("[data-am-plot][data-lx0]", card)) relabelAxis(svg);
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
      if (on) { placeAmLabels($("[data-am-plot]", panel)); centreAmPlot(panel); }
    }
  };
  const first = $(".am-panel:not([hidden]) [data-am-plot]", chart);
  if (first) { placeAmLabels(first); centreAmPlot(first.closest("[role=tabpanel]")); }
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
  const out = $("[data-random-out]", hero), die = $("[data-die]", hero), coin = $("[data-coin]", hero);
  const { random, min, max } = hero.dataset;
  const pick = () => (random === "coin" ? randInt(0, 1) : randInt(Number(min), Number(max)));
  const show = (n) => {
    out.textContent = random === "coin" ? (n ? "Heads" : "Tails") : String(n);
    if (die) die.dataset.face = n;
    if (coin) coin.textContent = n ? "H" : "T";
  };
  let spin = 0;
  // The die and the coin step through a few faces before they settle, like the rest of Archer's motion.
  const roll = () => {
    clearInterval(spin);
    const result = pick();
    if (!(die || coin) || matchMedia("(prefers-reduced-motion: reduce)").matches) return show(result);
    let steps = 6;
    hero.classList.add("is-rolling");
    spin = setInterval(() => {
      if (--steps <= 0) { clearInterval(spin); hero.classList.remove("is-rolling"); return show(result); }
      show(pick());
    }, 70);
  };
  show(pick());
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
  const display = $("[data-timer-display]", hero), startBtn = $("[data-timer-start]", hero), bar = $("[data-timer-bar]", hero);
  let mode = hero.dataset.timer, preset = Number(hero.dataset.seconds) || 300;
  let running = false, started = 0, banked = 0, raf = 0, audio = null;
  const elapsed = () => banked + (running ? (performance.now() - started) / 1000 : 0);
  const render = () => {
    const t = mode === "timer" ? Math.max(preset - elapsed(), 0) : elapsed();
    display.textContent = mode === "stopwatch" && t < 3600 ? `${clockText(t)}.${String(Math.floor((t % 1) * 10))}` : clockText(Math.ceil(t));
    if (bar) bar.style.setProperty("--p", mode === "timer" ? Math.min(elapsed() / preset, 1).toFixed(4) : 0);
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
    if (bar) bar.hidden = mode !== "timer";
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
    const share = principal / (emi * n);
    return { emi: money(emi), interest: money(emi * n - principal), total: money(emi * n), bar: share,
             principalShare: Number.isFinite(share) ? `${Math.round(share * 100)}%` : "", interestShare: Number.isFinite(share) ? `${100 - Math.round(share * 100)}%` : "" };
  },
  bmi: ({ height, weight }) => {
    const m = height / 100, bmi = weight / (m * m);
    const category = bmi < 18.5 ? "Underweight" : bmi < 25 ? "Healthy" : bmi < 30 ? "Overweight" : "Obese";
    return { bmi: Number.isFinite(bmi) ? bmi.toFixed(1) : "—", category, range: `${(18.5 * m * m).toFixed(1)}–${(24.9 * m * m).toFixed(1)} kg`,
             bar: (bmi - 15) / 25 };
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
    const out = calc(values);
    const bar = $("[data-fin-bar]", hero);
    if (bar && "bar" in out) bar.style.setProperty("--p", Number.isFinite(out.bar) ? Math.min(Math.max(out.bar, 0), 1).toFixed(4) : 0);
    for (const [key, value] of Object.entries(out)) {
      if (key === "bar") continue;
      const node = $(`[data-fin-out="${key}"]`, hero);
      if (node) node.textContent = value;
    }
  };
  hero.addEventListener("input", update);
  update();
}

// ------------------------------------------------- music players (songs/albums)
// Spotify and Apple Music players load with the page (lazily, as iframes); the YouTube video waits
// behind its thumbnail until played.
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
  $(".player-facade", pane)?.addEventListener("click", () => mountEmbed(pane));
  if (pane.dataset.kind === "spotify" || pane.dataset.kind === "applemusic") mountEmbed(pane);
}
for (const card of $$("[data-spotify-find]")) {
  const note = $(".watch-note", card);
  getJSON(`/api/spotify?title=${encodeURIComponent(card.dataset.spotifyFind)}&artist=${encodeURIComponent(card.dataset.spotifyArtist || "")}`).then((found) => {
    if (!found || !isHttp(found.embed)) { note.textContent = "Not found on Spotify automatically."; return; }
    // The player replaces the whole card; it carries its own Spotify branding and link.
    const pane = el("div", { class: "music-embed music-embed--spotify", "data-embed": found.embed, "data-kind": "spotify" });
    card.replaceWith(pane);
    mountEmbed(pane);
  });
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

// ------------------------------------------------------ edge proximity
// The point on a rect's edge nearest (x, y), how far away it is, and whether (x, y) is inside.
function edgePoint(rect, x, y) {
  const inside = x > rect.left && x < rect.right && y > rect.top && y < rect.bottom;
  if (!inside) {
    const px = Math.min(Math.max(x, rect.left), rect.right), py = Math.min(Math.max(y, rect.top), rect.bottom);
    return { x: px, y: py, d: Math.hypot(x - px, y - py), inside };
  }
  const gaps = [[x - rect.left, rect.left, y], [rect.right - x, rect.right, y], [y - rect.top, x, rect.top], [rect.bottom - y, x, rect.bottom]];
  const [d, px, py] = gaps.reduce((a, b) => (b[0] < a[0] ? b : a));
  return { x: px, y: py, d, inside };
}

// ------------------------------------------------------ dot-matrix fields
// A grid of rounded squares that is densest at the bottom and thins out and fades toward the top.
// The horizon curves: the fade starts lower in the middle and climbs higher at the edges. Each dot
// steps between a few ink levels at random intervals. Used on the home page and at the foot of
// the results page; it only animates while on screen. With `pointer`, cells near the cursor light
// up; `energy` (0..1, settable later) makes the bright flashes more frequent.
// `span` is the share of the canvas (from the bottom) the field fills; the cursor glow can use all of it.
// `palette` ([[colour, weight], ...]) gives each dot its own hue for when `tint` (0 or 1, settable
// later) is raised: the field cross-fades from grey ink to those colours, keeping every dot's shade.
// The coloured dots live on a second canvas, and the cross-fade is a CSS opacity transition between
// the two, so it runs on the compositor while both keep the field's stepped ~15fps redraw.
function dotField(canvas, { pitch = 12, sq = 9, density = 0.7, curve = 0.45, ink = 1, pointer = false, energy = 0, span = 1, palette = null } = {}) {
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const LEVELS = [0.05, 0.12, 0.24, 0.42, 0.7];
  let cols, fade, level, next, hue, sprite, sprites = [], dpr, visible = true, last = 0;
  let tintTarget = 0, drawn = false, fadingUntil = 0;
  const FADE = 900;
  const tinted = palette ? Object.assign(document.createElement("canvas"), { className: canvas.className, ariaHidden: "true" }) : null;
  const tctx = tinted?.getContext("2d");
  if (tinted) {
    canvas.after(tinted);
    tinted.style.opacity = "0";
    for (const c of [canvas, tinted]) c.style.transition = `opacity ${FADE}ms cubic-bezier(.4, 0, .2, 1)`;
  }
  // The cursor glow lives on its own canvas above the field and redraws every frame while it moves,
  // so it stays smooth while the field keeps its stepped ~15fps. Its position and strength ease
  // toward the pointer instead of jumping.
  const REACH = 110;
  const glow = pointer && !still ? Object.assign(document.createElement("canvas"), { className: canvas.className }) : null;
  const gctx = glow?.getContext("2d");
  let tx = 0, ty = 0, gx = 0, gy = 0, on = 0, target = 0, placed = false, painted = false, prev = 0;
  if (glow) {
    canvas.after(glow);
    addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect();
      tx = e.clientX - r.left; ty = e.clientY - r.top; target = 1;
      if (!placed) { gx = tx; gy = ty; placed = true; }   // first sighting: appear in place, don't fly in
    }, { passive: true });
    document.documentElement.addEventListener("pointerleave", () => { target = 0; });
    addEventListener("blur", () => { target = 0; });
  }

  const layout = () => {
    dpr = devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    if (tinted) { tinted.width = canvas.width; tinted.height = canvas.height; }
    if (glow) { glow.width = canvas.width; glow.height = canvas.height; painted = true; }
    cols = Math.ceil(w / pitch);
    const rows = Math.ceil(h / pitch), now = performance.now();
    const top = rows - Math.ceil(rows * span);           // rows above this stay empty
    fade = new Float32Array(cols * rows); level = new Float32Array(cols * rows); next = new Float64Array(cols * rows);
    hue = palette ? new Uint8Array(cols * rows) : null;
    for (let c = 0; c < cols; c++) {
      const u = ((c + 0.5) / cols) * 2 - 1;             // -1 at the left edge, 1 at the right
      const horizon = curve * (1 - u * u);              // how far down the field starts in this column
      for (let r = 0; r < rows; r++) {
        const depth = ((r - top + 1) / (rows - top) - horizon) / (1 - horizon);   // 0 at the horizon, 1 at the bottom
        if (depth <= 0) continue;
        const k = r * cols + c;
        // Fewer dots survive higher up, and the ones that do are fainter.
        fade[k] = Math.random() < density * depth ** 1.1 ? ink * depth ** 1.8 : 0;
        level[k] = LEVELS[(Math.random() * LEVELS.length) | 0];
        next[k] = now + Math.random() * 3000;
        if (hue) hue[k] = pick();
      }
    }
    const make = (colour) => {
      const c = document.createElement("canvas");
      c.width = c.height = Math.ceil(sq * dpr);
      const g = c.getContext("2d");
      g.scale(dpr, dpr); g.fillStyle = colour;
      g.beginPath(); g.roundRect(0, 0, sq, sq, sq * 0.25); g.fill();
      return c;
    };
    sprite = make("#ededed");
    sprites = palette ? palette.map(([colour]) => make(colour)) : [];
  };
  // A dot's hue, drawn by the palette's weights.
  const totalWeight = palette ? palette.reduce((n, [, w]) => n + w, 0) : 0;
  const pick = () => {
    let x = Math.random() * totalWeight, i = 0;
    while (i < palette.length - 1 && (x -= palette[i][1]) > 0) i++;
    return i;
  };

  const draw = (now) => {
    drawn = true;
    // Each layer is drawn only while it can be seen: always during a cross-fade, else by mode.
    const fading = now < fadingUntil, grey = tintTarget < 1 || fading, colour = tinted && (tintTarget > 0 || fading);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (tinted) { tctx.setTransform(1, 0, 0, 1, 0, 0); tctx.clearRect(0, 0, tinted.width, tinted.height); }
    const off = (pitch - sq) / 2, size = sprite.width;
    for (let k = 0; k < fade.length; k++) {
      if (!fade[k]) continue;
      if (!still && now >= next[k]) {
        // Mostly dim, sometimes a bright flash that holds only briefly.
        const i = Math.random() < 0.08 + energy * 0.14 ? LEVELS.length - 1 : (Math.random() * (LEVELS.length - 1)) | 0;
        level[k] = LEVELS[i];
        next[k] = now + (i === LEVELS.length - 1 ? 180 + Math.random() * 400 : 600 + Math.random() * 2800);
      }
      const a = level[k] * fade[k], x = Math.round(((k % cols) * pitch + off) * dpr), y = Math.round(((k / cols | 0) * pitch + off) * dpr);
      if (grey) { ctx.globalAlpha = a; ctx.drawImage(sprite, x, y, size, size); }
      // Coloured ink reads darker than grey at the same alpha, so it gets a small lift.
      if (colour) { tctx.globalAlpha = Math.min(1, a * 1.25); tctx.drawImage(sprites[hue[k]], x, y, size, size); }
    }
  };

  // The cursor lights a soft disc of cells, including ones the field leaves empty. Like the field,
  // the disc fades out toward the top of the page.
  const drawGlow = (now) => {
    const dt = Math.min(64, now - (prev || now)); prev = now;
    const k1 = 1 - Math.exp(-dt / 55), k2 = 1 - Math.exp(-dt / 140);
    gx += (tx - gx) * k1; gy += (ty - gy) * k1; on += (target - on) * k2;
    const settled = Math.abs(tx - gx) < 0.3 && Math.abs(ty - gy) < 0.3 && Math.abs(target - on) < 0.004;
    if (settled) { gx = tx; gy = ty; on = target; if (!painted) return; }   // at rest: nothing to redraw
    gctx.setTransform(1, 0, 0, 1, 0, 0);
    gctx.clearRect(0, 0, glow.width, glow.height);
    painted = !settled;                                     // one last frame once it comes to rest
    if (on < 0.004) return;
    const off = (pitch - sq) / 2, size = sprite.width, rows = fade.length / cols;
    const c0 = Math.max(0, Math.floor((gx - REACH) / pitch)), c1 = Math.min(cols - 1, Math.floor((gx + REACH) / pitch));
    const r0 = Math.max(0, Math.floor((gy - REACH) / pitch)), r1 = Math.min(rows - 1, Math.floor((gy + REACH) / pitch));
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
      const d = Math.hypot(c * pitch + pitch / 2 - gx, r * pitch + pitch / 2 - gy) / REACH;
      if (d >= 1) continue;
      const k = r * cols + c;
      const height = (r + 1) / rows;                        // fades linearly to nothing at the top edge
      // Smoothstep falloff: no hard rim as the disc slides across the grid.
      const fall = 1 - d * d * (3 - 2 * d);
      gctx.globalAlpha = Math.max(0, fall * 0.6 * height * on - level[k] * fade[k] * 0.5);
      gctx.drawImage(sprite, Math.round((c * pitch + off) * dpr), Math.round((r * pitch + off) * dpr), size, size);
    }
  };

  // The loop only runs while the field is on screen: scrolled away, no frames are requested at all.
  let looping = false;
  const tick = (now) => {
    if (!visible) { looping = false; return; }
    if (now - last > 66) { draw(now); last = now; }   // ~15fps: the motion is stepped anyway
    if (glow) drawGlow(now);
    requestAnimationFrame(tick);
  };
  const start = () => { if (!still && visible && !looping) { looping = true; requestAnimationFrame(tick); } };
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; start(); }).observe(canvas);
  layout();
  if (still) draw(performance.now()); else start();
  addEventListener("resize", () => { layout(); if (still) draw(performance.now()); });
  return {
    set energy(value) { energy = value; },
    set tint(value) {
      if (!tinted || value === tintTarget) return;
      tintTarget = value;
      fadingUntil = performance.now() + FADE + 100;
      // The first paint (a saved mode being restored) shows the right colours at once, without a fade.
      const instant = !drawn;
      for (const c of [canvas, tinted]) if (instant) c.style.transition = "none";
      canvas.style.opacity = String(1 - value);
      tinted.style.opacity = String(value);
      if (instant) requestAnimationFrame(() => requestAnimationFrame(() => {
        for (const c of [canvas, tinted]) c.style.transition = `opacity ${FADE}ms cubic-bezier(.4, 0, .2, 1)`;
      }));
      draw(performance.now());
    },
  };
}

// ------------------------------------------------------ AI loading and reveal
// The AI skeleton: lines of "text" drawn as the brand's rounded-square dots. Each dot holds one of
// a few stepped ink levels; a bright band sweeps diagonally across, and stray dots flash, so it
// reads as the pixel mark thinking. `lines` are line lengths as a share of the width; 0 is a gap.
// Returns { stop() }; the loop also ends by itself once the canvas leaves the page.
function pixelSkeleton(canvas, lines) {
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SQ = 4.5, PITCH = 6.5, ROWS = 2, LEAD = 9;        // two dot rows per line, LEAD px between lines
  const LEVELS = [0.07, 0.13, 0.22, 0.38, 0.62, 0.9];
  let width = 0, dpr = 1, dots = [], sprite = null, running = true, last = 0;
  const born = performance.now();
  const height = lines.reduce((h, len) => h + (len ? ROWS * PITCH : PITCH * 0.6) + LEAD, -LEAD);
  canvas.style.height = height + "px";

  const layout = () => {
    width = canvas.clientWidth; dpr = devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    const cols = Math.floor(width / PITCH);
    dots = [];
    let y = 0;
    for (const len of lines) {
      if (!len) { y += PITCH * 0.6 + LEAD; continue; }
      const end = Math.max(4, Math.round(cols * len));
      for (let r = 0; r < ROWS; r++) for (let c = 0; c < end; c++) {
        // The line's tail dissolves, like the mark's legs: fewer, fainter dots toward the end.
        const tail = Math.max(0, (c - (end - 7)) / 7);
        if (Math.random() < tail * 0.8) continue;
        dots.push({ x: c * PITCH, y: y + r * PITCH, c, row: Math.round(y / PITCH) + r, fade: 1 - tail * 0.6,
          level: LEVELS[(Math.random() * 3) | 0], next: 0, at: c * 9 + Math.random() * 260 });
      }
      y += ROWS * PITCH + LEAD;
    }
    sprite = document.createElement("canvas");
    sprite.width = sprite.height = Math.ceil(SQ * dpr);
    const g = sprite.getContext("2d");
    g.scale(dpr, dpr); g.fillStyle = "#ededed";
    g.beginPath(); g.roundRect(0, 0, SQ, SQ, SQ * 0.28); g.fill();
  };

  const draw = now => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const t = now - born, size = sprite.width;
    const span = width + 240, band = ((t / 1700) % 1) * span - 120;   // sweep position, px
    for (const d of dots) {
      if (t < d.at) continue;                                         // dots ignite left to right
      if (!still && now >= d.next) {
        const flash = Math.random() < 0.05;
        d.level = LEVELS[flash ? LEVELS.length - 1 : (Math.random() * 3) | 0];
        d.next = now + (flash ? 140 + Math.random() * 220 : 380 + Math.random() * 1600);
      }
      // The sweep leans: lower rows lag, so the band runs diagonally like the mark's wave.
      const reach = Math.abs(d.x - (band - d.row * PITCH * 1.4)) / 70;
      const lift = still ? 0 : Math.max(0, 1 - reach);
      const alpha = Math.max(d.level, LEVELS[Math.min(LEVELS.length - 1, Math.round(lift * (LEVELS.length - 1)))]);
      ctx.globalAlpha = alpha * d.fade * Math.min(1, (t - d.at) / 120);
      ctx.drawImage(sprite, Math.round(d.x * dpr), Math.round(d.y * dpr), size, size);
    }
  };

  const tick = now => {
    if (!running || !canvas.isConnected) return;
    if (canvas.clientWidth && canvas.clientWidth !== width) layout();
    if (width && now - last > 55) { draw(now); last = now; }          // ~18fps: the motion is stepped
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return { stop() { running = false; } };
}

// Decode a freshly rendered answer part: its text first shows as a field of pulsing dots, the dots
// turn into random letters that keep changing, and then the letters lock onto the real text in a
// wave that spreads outward from the top-left corner. Each character keeps its real width the whole
// time, so nothing reflows; when it is done the original text nodes come back.
function decodeReveal(root) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const WIDE = "ABCDEFGHKNPRSTUVXYZabcdeghknopqsuvxyz0123456789#*+=<>/";
  const NARROW = "iIl1!|:;.,'fjrt";
  const TICK = 60;                                                   // a scrambling letter changes every TICK ms
  const pick = set => set[(Math.random() * set.length) | 0];

  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT,
    { acceptNode: n => n.nodeValue.trim() && !n.parentElement.closest("svg, style, script") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const cells = [];
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    for (const part of node.nodeValue.split(/(\s+)/)) {
      if (!part) continue;
      if (!part.trim()) { frag.append(part); continue; }
      const word = document.createElement("span");
      word.className = "ai-gw";                                      // no-wrap: a word never breaks between letters
      for (const ch of part) {
        const cell = document.createElement("span");
        cell.className = "ai-gl";
        cell.textContent = ch;
        word.append(cell);
        cells.push({ node: cell, ch });
      }
      frag.append(word);
    }
    node.replaceWith(frag);
  }
  const restore = () => {
    for (const cell of cells) cell.node.textContent = cell.ch;
    root.querySelectorAll(".ai-gw").forEach(w => w.replaceWith(w.textContent));
    root.normalize();
    root.classList.remove("is-decoding");
    root.removeAttribute("aria-busy");
  };
  const box = root.getBoundingClientRect();
  if (!cells.length || !box.width) { restore(); return; }            // hidden: nothing to animate

  // One read pass, then one write pass, so the measuring costs a single layout.
  const rects = cells.map(c => c.node.getBoundingClientRect());
  const sizes = new Map();
  const far = Math.hypot(box.width, box.height * 1.6) || 1;
  const scale = Math.min(1.35, 0.75 + cells.length / 900);            // longer parts take a little longer
  cells.forEach((cell, i) => {
    const r = rects[i];
    const parent = cell.node.parentElement.parentElement;
    if (!sizes.has(parent)) sizes.set(parent, parseFloat(getComputedStyle(parent).fontSize) || 15);
    cell.set = r.width < sizes.get(parent) * 0.36 ? NARROW : WIDE;
    const d = Math.hypot(r.left - box.left, (r.top - box.top) * 1.6) / far;   // 0 at the corner, 1 at the far edge
    cell.dot = d * 260 * scale;
    cell.scramble = (300 + d * 520) * scale + Math.random() * 140;
    cell.settle = cell.scramble + (360 + d * 700) * scale + Math.random() * 200;
    cell.width = r.width;
  });
  root.classList.add("is-decoding");
  root.setAttribute("aria-busy", "true");
  for (const cell of cells) {
    cell.node.style.width = cell.width + "px";
    cell.node.style.setProperty("--d", (cell.dot / 1000).toFixed(3) + "s");
    cell.node.classList.add("is-wait");
    cell.phase = 0;
  }

  const start = performance.now();
  const end = Math.max(...cells.map(c => c.settle));
  let last = 0;
  const tick = now => {
    if (!root.isConnected || !cells[0].node.isConnected) return;     // re-rendered or removed meanwhile
    const t = now - start;
    if (t >= end) { restore(); return; }
    if (now - last >= TICK) {
      last = now;
      for (const cell of cells) {
        const n = cell.node;
        if (cell.phase === 3) continue;
        if (t >= cell.settle) { n.className = "ai-gl"; n.textContent = cell.ch; cell.phase = 3; }
        else if (t >= cell.scramble) {
          if (cell.phase !== 2) { n.className = "ai-gl is-scramble"; cell.phase = 2; }
          n.textContent = pick(cell.set);
        } else if (t >= cell.dot && cell.phase === 0) { n.className = "ai-gl is-dot"; cell.phase = 1; }
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

const homeField = $(".home-field");
// In AI Mode the field takes AI Mode's colours: the violet and blue of its glow, the periwinkle of
// its highlights, and a little of the green accent.
const AI_DOTS = [["#8f94ff", 0.34], ["#7c5cff", 0.28], ["#5ea8ff", 0.26], ["#7cc9a6", 0.12]];
const homeDots = homeField ? dotField(homeField, { span: 0.56, palette: AI_DOTS }) : null;

// ------------------------------------------------------------ home
// One search box, two destinations: Search goes to /search, AI Mode to /ai. AI Mode only *opens*
// the AI workspace; the paid run starts there because this submit (a user gesture) leaves a
// one-shot sessionStorage flag for that exact query. Reloads, history and prefetch never start it.
const home = $("[data-home]");
if (home) {
  const form = $(".searchbar--home", home), input = $("input[name='q']", form);
  const go = $(".searchbar-go", form), goLabel = [...go.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
  const buttons = $$("[data-home-mode]", home), thumb = $(".home-modes-thumb", home);
  const COPY = {
    search: { placeholder: "Search the web privately", go: "Search", action: "/search" },
    ai: { placeholder: "Ask Archer anything", go: "Ask", action: "/ai" },
  };
  let current = "search";
  const placeThumb = () => {
    const b = buttons.find((n) => n.dataset.homeMode === current);
    // The rule sits under the label text, inside the button's 10px padding.
    thumb.style.setProperty("--w", b.offsetWidth - 20 + "px");
    thumb.style.setProperty("--x", b.offsetLeft + 10 + "px");
  };
  const setMode = (value, { remember = true } = {}) => {
    current = value === "ai" ? "ai" : "search";
    document.body.classList.toggle("home-ai", current === "ai");
    buttons.forEach((b) => { const on = b.dataset.homeMode === current; b.classList.toggle("is-active", on); b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
    $$("[data-hint-for]", home).forEach((n) => { n.hidden = n.dataset.hintFor !== current; });
    input.placeholder = COPY[current].placeholder;
    if (goLabel) goLabel.textContent = COPY[current].go;
    form.action = COPY[current].action;
    placeThumb();
    if (homeDots) { homeDots.energy = current === "ai" ? 1 : 0; homeDots.tint = current === "ai" ? 1 : 0; }
    if (remember) try { localStorage.setItem("archer-home-mode", current); } catch { /* storage blocked */ }
  };
  buttons.forEach((b) => {
    b.addEventListener("click", () => { setMode(b.dataset.homeMode); input.focus(); });
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      setMode(current === "ai" ? "search" : "ai");
      buttons.find((n) => n.dataset.homeMode === current).focus();
    });
  });
  form.addEventListener("submit", (e) => {
    const q = input.value.trim();
    if (!q) { e.preventDefault(); input.focus(); return; }
    // Bangs are a search feature; they never go to AI.
    const ai = current === "ai" && !q.startsWith("!");
    form.action = ai ? "/ai" : "/search";
    if (ai) try { sessionStorage.setItem("archer-ai-autostart", q); } catch { /* AI Mode then waits for a click */ }
  });
  // Ctrl+Enter sends the query to the other mode without switching first.
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey) || e.isComposing) return;
    e.preventDefault(); e.stopImmediatePropagation();
    setMode(current === "ai" ? "search" : "ai", { remember: false });
    form.requestSubmit();
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === "KeyM") { e.preventDefault(); setMode(current === "ai" ? "search" : "ai"); }
  });

  // Pixel edge. Near the cursor the composer's outer band (drawn in CSS) fades out and this canvas
  // redraws that stretch as rounded dots in the composer's own colour: up to GROW px bigger than the
  // band right by the cursor, shrinking smoothly to the band's thickness further out, with up to SPILL
  // smaller rows dissolving outward from it near the cursor. The band is fully
  // open within NEAR px of the cursor's edge point and solid again by FAR px (its CSS mask uses the same numbers).
  // One rAF loop owns the motion: the edge point follows the cursor on a very short ease (no visible
  // lag, no jitter) and the strength fades in quickly and out a little slower. The loop writes the CSS
  // mask's variables and paints the dots from the same eased values, so the band and dots never disagree,
  // and it sleeps once everything has settled.
  const composer = form.parentElement;
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const dots = el("canvas", { class: "composer-dots", "aria-hidden": "true" });
    composer.append(dots);
    const g = dots.getContext("2d");
    const PAD = 20;                                     // the canvas overhangs the box so the spill rows are never clipped
    const RADIUS = 18, NEAR = 32, FAR = 104, GROW = 4.5, PITCH = 7;
    // Like the mark's legs, the edge dissolves outward: the band's own row of dots plus SPILL more rows
    // stepping out one PITCH at a time, each smaller and appearing only closer to the cursor, the
    // outermost one sparse (every other dot).
    const SPILL = 2;
    const OUTSIDE = 96, INSIDE = 22;                    // how far from the edge the cursor still counts
    const strength = (p) => { const t = Math.max(0, 1 - p.d / (p.inside ? INSIDE : OUTSIDE)); return t * t * (3 - 2 * t); };
    let tx = 0, ty = 0, target = 0, ex = 0, ey = 0, edge = 0, frame = 0, last = 0, awakeUntil = 0;

    const paint = () => {
      const w = form.offsetWidth, h = form.offsetHeight, dpr = devicePixelRatio || 1;
      const cw = Math.round((w + PAD * 2) * dpr), ch = Math.round((h + PAD * 2) * dpr);
      if (dots.width !== cw || dots.height !== ch) { dots.width = cw; dots.height = ch; }
      g.setTransform(dpr, 0, 0, dpr, PAD * dpr, PAD * dpr);
      g.clearRect(-PAD, -PAD, w + PAD * 2, h + PAD * 2);
      if (edge < 0.003) return;
      const css = getComputedStyle(composer), bw = parseFloat(css.getPropertyValue("--bw")) || 2;
      g.fillStyle = css.getPropertyValue("--fill").trim();
      // Dots sit on the band's centre line (inset bw/2), walked clockwise from the top-left. Their
      // count comes from a fixed reference outline, so they never reshuffle when focus widens the band.
      const outline = (inset) => {
        const r = RADIUS - inset, x0 = inset, y0 = inset, x1 = w - inset, y1 = h - inset;
        const sw = x1 - x0 - 2 * r, sh = y1 - y0 - 2 * r, q = (Math.PI * r) / 2;
        // Each step returns a point and the outward normal there.
        const turn = (cx, cy, from) => (t) => { const a = from + t / r, c = Math.cos(a), n = Math.sin(a); return [cx + r * c, cy + r * n, c, n]; };
        return [
          [sw, (t) => [x0 + r + t, y0, 0, -1]], [q, turn(x1 - r, y0 + r, -Math.PI / 2)],
          [sh, (t) => [x1, y0 + r + t, 1, 0]], [q, turn(x1 - r, y1 - r, 0)],
          [sw, (t) => [x1 - r - t, y1, 0, 1]], [q, turn(x0 + r, y1 - r, Math.PI / 2)],
          [sh, (t) => [x0, y1 - r - t, -1, 0]], [q, turn(x0 + r, y0 + r, Math.PI)],
        ];
      };
      const length = (segs) => segs.reduce((n, [len]) => n + len, 0);
      const segs = outline(bw / 2), total = length(segs);
      g.setTransform(1, 0, 0, 1, 0, 0);                   // dots are placed in device pixels
      const count = Math.round(length(outline(1)) / PITCH), step = total / count;
      // Every dot is fully opaque in the exact fill colour, so it reads as part of the composer (a
      // translucent dot over the darker page would come out a different grey). Only size falls off:
      // big by the cursor, back to the band's own thickness by FAR px, where it merges into the band.
      let seg = 0, start = 0;
      for (let i = 0; i < count; i++) {
        const at = i * step;
        while (at - start > segs[seg][0] && seg < segs.length - 1) { start += segs[seg][0]; seg++; }
        const [x, y, nx, ny] = segs[seg][1](at - start);
        const d = Math.hypot(x - ex, y - ey);
        if (d >= FAR) continue;
        const t = 1 - d / FAR, k = edge * t * t * (3 - 2 * t);
        for (let row = 0; row <= SPILL; row++) {
          if (row === SPILL && i % 2) continue;              // the outermost row is sparse
          // Row 0 is the band itself; outer rows need a stronger pull to appear and stay smaller.
          const need = row * 0.24, kr = row ? (k - need) / (1 - need) : k;
          if (kr <= 0) continue;
          const reach = row ? kr : 1, base = row ? bw * 0.8 : bw;
          // Drawn in whole device pixels: a dot this small, left between pixels, is mostly
          // anti-aliased edge and reads darker than the fill around it.
          const size = Math.round((base + GROW * kr) * (1 - row * 0.3) * reach * dpr);
          if (size < 1) continue;
          const ox = x + nx * row * PITCH, oy = y + ny * row * PITCH;
          const px = Math.round((ox + PAD) * dpr - size / 2), py = Math.round((oy + PAD) * dpr - size / 2);
          g.beginPath();
          g.roundRect(px, py, size, size, size >= 5 ? size * 0.3 : 0);
          g.fill();
        }
      }
    };

    const tick = (now) => {
      frame = 0;
      const dt = Math.min(48, now - (last || now)); last = now;
      if (edge < 0.003) { ex = tx; ey = ty; }           // appear where the cursor is, never glide in
      const kp = 1 - Math.exp(-dt / 32), ke = 1 - Math.exp(-dt / (target > edge ? 60 : 140));
      ex += (tx - ex) * kp; ey += (ty - ey) * kp; edge += (target - edge) * ke;
      const settled = Math.abs(tx - ex) < 0.15 && Math.abs(ty - ey) < 0.15 && Math.abs(target - edge) < 0.002 && now >= awakeUntil;
      if (settled) { ex = tx; ey = ty; edge = target; }
      composer.style.setProperty("--ex", ex.toFixed(2) + "px");
      composer.style.setProperty("--ey", ey.toFixed(2) + "px");
      composer.style.setProperty("--edge", edge.toFixed(4));
      paint();
      if (settled) last = 0; else frame = requestAnimationFrame(tick);
    };
    // `ms` keeps the loop running through a CSS transition (band width or colour) so the dots track it.
    const wake = (ms = 0) => { awakeUntil = Math.max(awakeUntil, performance.now() + ms); if (!frame) frame = requestAnimationFrame(tick); };

    addEventListener("pointermove", (e) => {
      const rect = form.getBoundingClientRect(), p = edgePoint(rect, e.clientX, e.clientY);
      tx = p.x - rect.left; ty = p.y - rect.top; target = strength(p);
      wake();
    }, { passive: true });
    const leave = () => { target = 0; wake(); };
    document.documentElement.addEventListener("pointerleave", leave);
    addEventListener("blur", leave);
    form.addEventListener("focusin", () => wake(260));
    form.addEventListener("focusout", () => wake(260));
    new MutationObserver(() => wake(340)).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    addEventListener("resize", () => wake());
  }

  // Starter chips fill the box and submit it in the current mode (so AI Mode keeps its one-shot flag).
  $$("[data-home-try]", home).forEach((b) => b.addEventListener("click", () => {
    input.value = b.dataset.homeTry;
    form.requestSubmit();
  }));

  // Greeting by local time of day.
  const greeting = $("[data-home-greeting]");
  const tick = () => {
    const h = new Date().getHours();
    greeting.textContent = h < 5 ? "Up late?" : h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";
  };
  tick();
  setInterval(tick, 15000);

  let saved = "search";
  try { saved = localStorage.getItem("archer-home-mode") || "search"; } catch { /* default */ }
  setMode(saved, { remember: false });
  // Animate only real switches, not the restore of a saved mode on first paint.
  requestAnimationFrame(() => requestAnimationFrame(() => $(".home-modes", home).classList.add("is-ready")));
  addEventListener("resize", placeThumb);
  document.fonts?.ready.then(placeThumb);
}
const serpField = $(".serp-field");
if (serpField) dotField(serpField, { pitch: 10, sq: 7.5, density: 0.55, curve: 0.55, ink: 0.8 });
