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

// History page: remove entries, clear a range, pause, and set site preferences.
const historyPage = $("[data-history-page]");
if (historyPage) {
  historyPage.addEventListener("click", async (e) => {
    const removeSearch = e.target.closest("[data-remove-search]");
    const removeClick = e.target.closest("[data-remove-click]");
    const siteSet = e.target.closest("[data-site-set]");
    try {
      if (removeSearch) {
        await historyPost("/api/history/delete", { searches: [Number(removeSearch.dataset.removeSearch)] });
        const entry = removeSearch.closest(".h-entry"), list = entry.parentElement;
        entry.remove();
        if (!list.children.length) list.closest(".h-day")?.remove();
      } else if (removeClick) {
        await historyPost("/api/history/delete", { clicks: [Number(removeClick.dataset.removeClick)] });
        removeClick.closest(".h-click").remove();
      } else if (siteSet) {
        const row = siteSet.closest("[data-site]");
        await historyPost("/api/history/site", { host: row.dataset.site, pref: siteSet.dataset.siteSet || null });
        $$("[data-site-set]", row).forEach((b) => b.setAttribute("aria-pressed", String(b === siteSet)));
      }
    } catch (error) { alert(error.message); }
  });
  const clearButton = $("[data-history-clear]", historyPage);
  let confirming = 0;
  clearButton?.addEventListener("click", async () => {
    const range = $("[data-history-range]", historyPage).value;
    if (!confirming) {
      clearButton.textContent = range === "all" ? "Clear everything?" : "Confirm clear";
      confirming = setTimeout(() => { confirming = 0; clearButton.textContent = "Clear"; }, 3500);
      return;
    }
    clearTimeout(confirming);
    try { await historyPost("/api/history/clear", { range }); location.reload(); }
    catch (error) { clearButton.textContent = "Clear"; confirming = 0; alert(error.message); }
  });
  const toggle = $("[data-history-toggle]", historyPage);
  toggle?.addEventListener("click", async () => {
    try { await historyPost("/api/settings", { values: { history: toggle.dataset.historyToggle } }); location.reload(); }
    catch (error) { alert(error.message); }
  });
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
// On a phone the plot scrolls sideways: open it on the searched model, not the cheap or old end.
function centreAmPlot(panel) {
  const wrap = $(".am-plot-wrap", panel), self = $(".am-mark--self .am-dot", panel);
  if (!wrap || !self || wrap.scrollWidth <= wrap.clientWidth) return;
  const dot = self.getBoundingClientRect(), box = wrap.getBoundingClientRect();
  wrap.scrollLeft += dot.left + dot.width / 2 - (box.left + box.width / 2);
}
function setAmToggle(button, on) {
  const panel = button.closest("[role=tabpanel]");
  const key = button.dataset.amToggle;
  const target = key === "dots" ? $("[data-am-dots]", panel) : $(`[data-mark="${key}"]`, panel);
  button.setAttribute("aria-pressed", String(on));
  target?.classList.toggle("is-off", !on);
}
function redrawAmPlot(panel) {
  const svg = $("[data-am-plot]", panel);
  placeAmLabels(svg);
  drawAmFrontier(svg);
}
for (const button of $$("[data-am-toggle]")) {
  button.addEventListener("click", () => {
    setAmToggle(button, button.getAttribute("aria-pressed") !== "true");
    redrawAmPlot(button.closest("[role=tabpanel]"));
  });
}
// "Show all" brings every model back; "Only …" keeps the searched model and hides the rest.
for (const button of $$("[data-am-show]")) {
  button.addEventListener("click", () => {
    const panel = button.closest("[role=tabpanel]");
    for (const t of $$("[data-am-table] [data-am-toggle]", panel)) {
      setAmToggle(t, button.dataset.amShow === "all" || t.classList.contains("am-toggle--self"));
    }
    redrawAmPlot(panel);
  });
}

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

// ------------------------------------------- AI model card: currency and cost estimator
// Prices arrive in US dollars (data-money); the card shows them in the home currency or USD,
// remembered per browser. aimodels.money is the server-side twin of fmtMoney.
for (const card of $$("[data-am-card]")) {
  const home = card.dataset.fxCode ? { code: card.dataset.fxCode, symbol: card.dataset.fxSymbol, rate: +card.dataset.fxRate } : null;
  const USD = { code: "USD", symbol: "$", rate: 1 };
  const KEY = "archer.aimodel.currency";
  let current = home || USD;
  try { if (home && localStorage.getItem(KEY) === "USD") current = USD; } catch { /* storage blocked: keep the default */ }
  const other = () => (current === USD ? home : USD);

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
    if (label) label.textContent = `API price, ${current.symbol.trim()} per million tokens (3:1 input:output)`;
  };

  const estimate = $("[data-am-estimate]", card);
  const pricing = $("[data-am-pricing]", card);
  const field = (name) => Math.max(0, parseFloat($(`[data-am-field="${name}"]`, estimate).value) || 0);
  const updateEstimate = () => {
    if (!estimate) return;
    const n = Math.max(1, field("n"));
    const costIn = field("in") * +pricing.dataset.in / 1e6, costOut = field("out") * +pricing.dataset.out / 1e6;
    const per = costIn + costOut;
    for (const [side, cost] of [["in", costIn], ["out", costOut]]) {
      const value = $(`[data-am-split="${side}"]`, estimate);
      value.dataset.money = cost * n;
      value.textContent = fmtMoney(cost * n, current);
      $(`[data-am-split-bar="${side}"]`, estimate).style.flex = String(cost || 0.0001);
    }
    const total = $("[data-am-total-val]", estimate);
    total.dataset.money = per * n;
    total.textContent = fmtMoney(per * n, current);
    const alt = other();
    $("[data-am-total-sub]", estimate).textContent = [n > 1 ? `${fmtMoney(per, current)} per request` : "",
      alt ? `≈ ${fmtMoney(per * n, alt)}` : ""].filter(Boolean).join(" · ");
  };

  const apply = () => {
    for (const node of $$("[data-money]", card)) {
      if (node.hasAttribute("data-am-total-val") || node.hasAttribute("data-am-split")) continue;
      const cur = node.hasAttribute("data-money-alt") ? other() : current;
      node.textContent = cur ? fmtMoney(+node.dataset.money, cur) + (node.dataset.suffix || "") : "";
    }
    for (const svg of $$("[data-am-plot][data-lx0]", card)) relabelAxis(svg);
    for (const b of $$("[data-am-cur]", card)) b.setAttribute("aria-pressed", String(b.dataset.amCur === current.code));
    updateEstimate();
  };

  for (const b of $$("[data-am-cur]", card)) {
    b.addEventListener("click", () => {
      current = b.dataset.amCur === "USD" ? USD : home;
      try { localStorage.setItem(KEY, current.code); } catch { /* not remembered; still switches */ }
      apply();
    });
  }
  if (estimate) {
    const presets = $$("[data-am-preset]", estimate);
    for (const b of presets) {
      b.addEventListener("click", () => {
        const [i, o, n] = b.dataset.amPreset.split(",");
        $('[data-am-field="in"]', estimate).value = i;
        $('[data-am-field="out"]', estimate).value = o;
        $('[data-am-field="n"]', estimate).value = n;
        presets.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        updateEstimate();
      });
    }
    for (const input of $$("[data-am-field]", estimate)) {
      input.addEventListener("input", () => {
        const now = [field("in"), field("out"), Math.max(1, field("n"))].join(",");
        presets.forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.amPreset === now)));
        updateEstimate();
      });
    }
  }
  apply();
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
