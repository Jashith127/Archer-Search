// Archer companion: the settings window (/app) and first-run setup (/app/welcome).
// Typed fields and provider cards collect in a save bar (the app) or save per step (setup);
// switches like "Use AI" and "Start with Windows" apply at once. Keys never come back to the page:
// it only knows whether one is set and its last four characters.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let state = JSON.parse($("#settings-state").textContent);
  const csrf = document.body.dataset.csrf;
  const isSetup = document.body.classList.contains("c-welcome");
  const rows = $$("[data-field]");
  const toastBox = $("[data-toast]");
  let toastTimer = 0;

  const toast = (text, bad = false) => {
    toastBox.textContent = text;
    toastBox.classList.toggle("is-bad", bad);
    toastBox.classList.add("is-shown");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastBox.classList.remove("is-shown"), bad ? 5200 : 2600);
  };
  const post = async (path, data = {}) => {
    const response = await fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Archer-CSRF": csrf }, body: JSON.stringify(data) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok && response.status !== 422) {
      throw new Error(typeof body.detail === "string" ? body.detail : "Archer couldn't complete that. Reload and try again.");
    }
    return { status: response.status, body };
  };
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };
  const fieldState = name => state.fields.find(f => f.name === name) || {};
  const aiDefaults = name => state.providers.ai.defaults[name] || { model: "", models: [], key: "" };

  // Logos come through Archer's favicon proxy; a miss shows the provider's initial instead.
  for (const img of $$("img[data-logo]")) {
    const fail = () => { img.closest(".c-logo")?.classList.add("is-letter"); img.remove(); };
    img.addEventListener("error", fail);
    if (img.complete && !img.naturalWidth) fail();
  }

  // ---------------------------------------------------------------- field values
  const valueOf = row => {
    const kind = row.dataset.kind;
    if (kind === "cards") return $("[data-input]:checked", row)?.value ?? "";
    if (kind === "switch") return $("[data-input]", row).checked ? "true" : "false";
    const input = $("[data-input]", row);
    if (kind === "secret") {
      if (row.classList.contains("is-removing")) return "";
      return input.value.trim() || undefined;
    }
    return input.value.trim();
  };
  const current = name => {
    const row = rows.find(r => r.dataset.field === name && !r.closest("[hidden]")) || rows.find(r => r.dataset.field === name);
    return row ? valueOf(row) : (fieldState(name).value ?? "");
  };
  const isDirty = row => {
    if (row.hasAttribute("data-locked")) return false;
    const value = valueOf(row);
    if (row.dataset.kind === "secret") return value !== undefined;
    return value !== (fieldState(row.dataset.field).value ?? "");
  };
  const typedSecret = name => {
    const row = rows.find(r => r.dataset.field === name);
    return row ? $("[data-input]", row).value.trim() : "";
  };
  const setError = (row, message) => {
    const error = $("[data-error]", row);
    if (!error) return;
    error.textContent = message || "";
    error.hidden = !message;
    row.classList.toggle("has-error", Boolean(message));
  };
  const setValue = (name, value) => {
    for (const row of rows.filter(r => r.dataset.field === name)) {
      if (row.dataset.kind === "cards") {
        const radio = $(`[data-input][value="${CSS.escape(value)}"]`, row);
        if (radio) radio.checked = true;
      } else if (row.dataset.kind === "switch") {
        $("[data-input]", row).checked = value === "true";
      } else if (row.dataset.kind !== "secret") {
        const input = $("[data-input]", row);
        if (input.tagName === "SELECT" && ![...input.options].some(o => o.value === value)) continue;
        input.value = value;
      }
    }
  };

  // ------------------------------------------------- conditional rows (data-when)
  // "ai_provider=openai,anthropic;google_backend=serper": every clause must match.
  const matches = rule => rule.split(";").every(clause => {
    const [name, values] = clause.split("=");
    return values.split(",").includes(current(name));
  });
  const applyWhen = () => {
    for (const node of $$("[data-when]")) node.hidden = !matches(node.dataset.when);
  };

  // ------------------------------------------------------- provider details
  const renderModelHints = (fromProviderChange = false) => {
    const provider = current("ai_provider");
    const defaults = aiDefaults(provider);
    for (const row of rows.filter(r => r.dataset.kind === "model")) {
      const input = $("[data-input]", row);
      if (fromProviderChange) input.value = "";
      input.placeholder = defaults.model ? defaults.model : provider === "ollama" ? "Load models to choose one" : "Model name";
      const list = $("[data-models]", row);
      if (!list.dataset.loadedFor || list.dataset.loadedFor !== provider) {
        list.replaceChildren(...defaults.models.map(model => el("option", { value: model })));
        list.dataset.loadedFor = "";
      }
      const result = $("[data-test-result]", row);
      if (fromProviderChange && result) { result.textContent = ""; result.className = "c-result"; }
    }
  };
  const renderCardStatus = () => {
    const keyed = name => fieldState(name).set;
    const status = {
      deepseek: keyed("deepseek_api_key"), openai: keyed("openai_api_key"), anthropic: keyed("anthropic_api_key"),
      openrouter: keyed("openrouter_api_key"), custom: Boolean(fieldState("ai_base_url").value) && current("ai_provider") === "custom",
      brave: keyed("brave_api_key"), google: keyed("serper_api_key") || (keyed("google_api_key") && Boolean(fieldState("google_cx").value)),
    };
    for (const node of $$("[data-card-status]")) {
      const id = node.dataset.cardStatus;
      node.textContent = status[id] ? (id === "custom" ? "Configured" : "Key saved") : "";
    }
  };

  // ------------------------------------------------------------------ secrets
  const renderSecret = row => {
    const item = fieldState(row.dataset.field);
    const box = $("[data-secret]", row);
    box.dataset.set = String(Boolean(item.set));
    box.classList.remove("is-editing", "is-removing");
    row.classList.remove("is-removing");
    $("[data-secret-hint]", row).textContent = item.set ? item.hint : "Not set";
    $(".c-secret-entry", row).hidden = true;
    $("[data-input]", row).value = "";
    const edit = $("[data-secret-edit]", row), remove = $("[data-secret-remove]", row);
    if (edit) edit.textContent = item.set ? "Replace" : "Add key";
    if (remove) { remove.hidden = !item.set; remove.textContent = "Remove"; }
  };
  const openSecret = row => {
    const box = $("[data-secret]", row);
    row.classList.remove("is-removing");
    box.classList.remove("is-removing");
    box.classList.add("is-editing");
    $(".c-secret-entry", row).hidden = false;
    const edit = $("[data-secret-edit]", row);
    if (edit) edit.textContent = "Cancel";
    const remove = $("[data-secret-remove]", row);
    if (remove) remove.hidden = true;
  };

  // ------------------------------------------------------------- dirty state
  const savebar = $("[data-savebar]");
  const refreshDirty = () => {
    applyWhen();
    if (!savebar) return;
    const dirty = rows.filter(isDirty);
    rows.forEach(row => row.classList.toggle("is-dirty", dirty.includes(row)));
    const names = new Set(dirty.map(row => row.dataset.field));
    $("[data-dirty-count]").textContent = names.size === 1 ? "1 change" : names.size + " changes";
    savebar.hidden = !names.size;
  };
  const discard = () => {
    for (const row of rows) {
      setError(row, "");
      if (row.dataset.kind === "secret") renderSecret(row);
      else setValue(row.dataset.field, fieldState(row.dataset.field).value ?? "");
    }
    renderModelHints();
    refreshDirty();
  };

  for (const row of rows) {
    for (const input of $$("[data-input]", row)) {
      input.addEventListener("input", () => { setError(row, ""); refreshDirty(); });
      input.addEventListener("change", () => {
        if (row.dataset.field === "ai_provider") {
          renderModelHints(true);
          applyWhen();
          // Asking a local Ollama for its models is free, so do it straight away.
          const load = current("ai_provider") === "ollama" && $$("[data-load-models]").find(b => !b.closest("[hidden]"));
          if (load) runTest(load, true);
        }
        // Twin rows (the same setting shown twice) follow each other.
        if (row.dataset.kind !== "secret") {
          for (const twin of rows.filter(r => r !== row && r.dataset.field === row.dataset.field)) {
            if (twin.dataset.kind === row.dataset.kind) setValue(row.dataset.field, valueOf(row));
          }
        }
        refreshDirty();
      });
    }
    if (row.dataset.kind !== "secret") continue;
    const input = $("[data-input]", row);
    $("[data-secret-edit]", row)?.addEventListener("click", () => {
      if ($("[data-secret]", row).classList.contains("is-editing")) {
        renderSecret(row); setError(row, ""); refreshDirty(); return;
      }
      openSecret(row);
      input.focus();
    });
    $("[data-secret-remove]", row)?.addEventListener("click", () => {
      const removing = !row.classList.contains("is-removing");
      row.classList.toggle("is-removing", removing);
      $("[data-secret]", row).classList.toggle("is-removing", removing);
      $("[data-secret-remove]", row).textContent = removing ? "Keep" : "Remove";
      refreshDirty();
    });
    $("[data-reveal]", row).addEventListener("click", event => {
      const shown = input.type === "text";
      input.type = shown ? "password" : "text";
      event.currentTarget.setAttribute("aria-label", shown ? "Show key" : "Hide key");
    });
  }
  // In setup, an unset key opens ready to paste.
  if (isSetup) {
    for (const row of rows.filter(r => r.dataset.kind === "secret" && !fieldState(r.dataset.field).set && !r.hasAttribute("data-locked"))) {
      openSecret(row);
      $("[data-secret-edit]", row).hidden = true;  // nothing to cancel back to
    }
  }

  const saveRows = async (subset, quiet = false) => {
    const dirty = subset.filter(isDirty);
    if (!dirty.length) return true;
    const values = {};
    for (const row of dirty) values[row.dataset.field] = valueOf(row);
    const { status, body } = await post("/api/settings", { values });
    if (status === 422) {
      let first = null;
      for (const [name, message] of Object.entries(body.errors || {})) {
        const row = subset.find(r => r.dataset.field === name && !r.closest("[hidden]")) || rows.find(r => r.dataset.field === name);
        if (row) { setError(row, message); first ||= row; }
      }
      first?.scrollIntoView({ block: "center", behavior: "smooth" });
      if (!quiet) toast("Some settings need a look before they can be saved.", true);
      return false;
    }
    state = body.state;
    for (const row of subset) {
      setError(row, "");
      if (row.dataset.kind === "secret") renderSecret(row);
    }
    renderState();
    if (!quiet) toast(body.restart ? "Saved. Restart Archer to apply the new address." : "Saved and applied.");
    return true;
  };

  if (savebar) {
    const save = async () => {
      const button = $("[data-save]");
      button.disabled = true;
      try {
        if (await saveRows(rows)) discard();
      } catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    };
    $("[data-save]").addEventListener("click", save);
    $("[data-discard]").addEventListener("click", discard);
    document.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
    });
    addEventListener("beforeunload", event => { if (rows.some(isDirty)) event.preventDefault(); });
  }

  // ---------------------------------------------------------- connection tests
  const visibleInput = name => {
    const row = rows.find(r => r.dataset.field === name && !r.closest("[hidden]"));
    return row ? $("[data-input]", row).value.trim() : "";
  };
  const runTest = async (button, silent = false) => {
    const row = button.closest(".c-row");
    const result = $("[data-test-result]", row);
    const target = button.dataset.test;
    const payload = { target, value: "" };
    if (target === "ai") {
      const provider = current("ai_provider");
      payload.provider = provider;
      payload.value = typedSecret(aiDefaults(provider).key || "");
      payload.base = visibleInput("ai_base_url");
      payload.model = visibleInput("ai_model");
    } else if (target === "google") {
      payload.value = typedSecret("google_api_key");
      payload.extra = visibleInput("google_cx");
    } else if (row.dataset.kind === "secret") {
      payload.value = typedSecret(row.dataset.field);
    } else if (row.dataset.field) {
      payload.value = $("[data-input]", row)?.value.trim() || "";
    }
    button.disabled = true;
    if (result) { result.className = "c-result is-busy"; result.textContent = "Checking…"; }
    try {
      const { body } = await post("/api/settings/test", payload);
      if (result) { result.className = "c-result " + (body.ok ? "is-ok" : "is-bad"); result.textContent = body.message; }
      if (body.models?.length) {
        const provider = current("ai_provider");
        for (const list of $$("[data-models]")) {
          list.replaceChildren(...body.models.map(model => el("option", { value: model })));
          list.dataset.loadedFor = provider;
        }
        // Ollama has no default model: pick the first installed one so setup can continue.
        const model = rows.find(r => r.dataset.kind === "model" && !r.closest("[hidden]"));
        if (provider === "ollama" && model && !$("[data-input]", model).value) {
          $("[data-input]", model).value = body.models[0];
          refreshDirty();
        }
        if (model) renderModelSelect(model, body.models);
      }
      return body;
    } catch (error) {
      if (result) { result.className = "c-result is-bad"; result.textContent = error.message; }
      if (!silent) toast(error.message, true);
      return { ok: false };
    } finally { button.disabled = false; }
  };
  // A short list of choices reads better as chips than a datalist hidden behind a click.
  const renderModelSelect = (row, models) => {
    let chips = $(".c-chips", row);
    if (!chips) { chips = el("div", { className: "c-chips" }); row.append(chips); }
    const input = $("[data-input]", row);
    const shown = models.slice(0, 12);
    chips.replaceChildren(...shown.map(model => {
      const chip = el("button", { type: "button", className: "c-chip", textContent: model });
      chip.classList.toggle("is-on", input.value === model);
      chip.addEventListener("click", () => {
        input.value = model;
        $$(".c-chip", chips).forEach(c => c.classList.toggle("is-on", c === chip));
        refreshDirty();
      });
      return chip;
    }));
    if (models.length > shown.length) chips.append(el("span", { className: "c-chip-more", textContent: `+${models.length - shown.length} more in the list` }));
  };
  for (const button of $$("[data-test]")) button.addEventListener("click", () => runTest(button));

  // ---------------------------------------------------------- instant switches
  const consent = $("[data-ai-consent]");
  const modeInputs = $$('input[name="ai-mode"]');
  const syncAI = () => {
    if (consent) consent.checked = state.ai.consent;
    if (!isSetup) modeInputs.forEach(input => { input.checked = input.value === state.ai.mode; input.disabled = !state.ai.consent; });
  };
  const savePreferences = async (nextConsent, nextMode, quiet = false) => {
    const { body } = await post("/api/ai/preferences", { consent: nextConsent, mode: nextConsent ? nextMode : "classic" });
    state.ai.consent = body.consent;
    state.ai.mode = body.default_mode;
    if (!quiet) toast(!body.consent ? "AI is off. Nothing will be sent." : body.default_mode === "balanced" ? "Balanced answers will start automatically." : "Saved. AI starts only when you ask.");
    syncAI();
    renderTiles();
  };
  if (!isSetup) {
    consent?.addEventListener("change", () => savePreferences(consent.checked, state.ai.mode).catch(error => { toast(error.message, true); syncAI(); }));
    modeInputs.forEach(input => input.addEventListener("change", () => savePreferences(true, input.value).catch(error => { toast(error.message, true); syncAI(); })));
  }

  for (const box of $$("[data-local]")) {
    const key = box.dataset.local;
    try { box.checked = (localStorage.getItem(key) ?? box.dataset.localDefault) === "1"; } catch { box.disabled = true; }
    box.addEventListener("change", () => {
      try { localStorage.setItem(key, box.checked ? "1" : "0"); toast("Saved."); }
      catch { toast("This window isn't keeping site data.", true); }
    });
  }

  const setAutostart = async enabled => {
    const { body } = await post("/api/settings/autostart", { enabled });
    state.autostart = body;
    for (const box of $$("[data-autostart]")) box.checked = body.enabled;
    renderTiles();
    return body;
  };
  for (const box of $$("[data-autostart]:not([data-autostart-setup])")) {
    box.addEventListener("change", async () => {
      try {
        const body = await setAutostart(box.checked);
        toast(body.enabled ? "Archer will start when you sign in." : "Archer won't start automatically.");
      } catch (error) { box.checked = !box.checked; toast(error.message, true); }
    });
  }

  // ------------------------------------------------------------ browser + copy
  for (const button of $$("[data-browser]")) {
    button.addEventListener("click", async () => {
      try {
        await post("/api/app/browser", { browser: button.dataset.browser });
        toast("Opened. Add Archer under Site search, then make it the default.");
      } catch (error) { toast(error.message, true); }
    });
  }
  for (const button of $$("[data-copy]")) {
    const label = button.lastChild;
    button.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(button.dataset.copy); label.textContent = "Copied"; button.classList.add("is-done"); }
      catch { label.textContent = "Select and copy"; }
      setTimeout(() => { label.textContent = "Copy"; button.classList.remove("is-done"); }, 1500);
    });
  }

  // ---------------------------------------------------------------- detection
  // Time zone and browser language only; nothing is looked up online.
  const ZONES = { "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Berlin": "DE",
    "Europe/Paris": "FR", "Europe/Madrid": "ES", "Europe/Rome": "IT", "Europe/Amsterdam": "NL", "Europe/Stockholm": "SE",
    "Europe/Oslo": "NO", "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI", "Europe/Zurich": "CH", "Europe/Vienna": "AT",
    "Europe/Brussels": "BE", "Europe/Warsaw": "PL", "Europe/Lisbon": "PT", "Europe/Istanbul": "TR", "Asia/Singapore": "SG",
    "Asia/Dubai": "AE", "Asia/Tokyo": "JP", "Asia/Seoul": "KR", "Asia/Shanghai": "CN", "Asia/Hong_Kong": "HK", "Asia/Taipei": "TW",
    "Asia/Bangkok": "TH", "Asia/Kuala_Lumpur": "MY", "Asia/Jakarta": "ID", "Asia/Manila": "PH", "Asia/Karachi": "PK",
    "Asia/Dhaka": "BD", "Asia/Colombo": "LK", "Asia/Kathmandu": "NP", "Asia/Riyadh": "SA", "Africa/Johannesburg": "ZA",
    "Africa/Lagos": "NG", "Africa/Nairobi": "KE", "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Brisbane": "AU",
    "Australia/Perth": "AU", "Pacific/Auckland": "NZ", "America/Toronto": "CA", "America/Vancouver": "CA", "America/Sao_Paulo": "BR",
    "America/Mexico_City": "MX", "America/Argentina/Buenos_Aires": "AR", "America/New_York": "US", "America/Chicago": "US",
    "America/Denver": "US", "America/Los_Angeles": "US", "America/Phoenix": "US" };
  const CURRENCY = { IN: "inr", US: "usd", GB: "gbp", CA: "cad", AU: "aud", NZ: "nzd", SG: "sgd", AE: "aed", JP: "jpy", KR: "krw",
    CN: "cny", HK: "hkd", CH: "chf", SE: "sek", BR: "brl", ZA: "zar", MX: "mxn", TR: "try", PL: "pln",
    DE: "eur", FR: "eur", ES: "eur", IT: "eur", NL: "eur", IE: "eur", AT: "eur", BE: "eur", FI: "eur", PT: "eur" };
  // onlyDefaults: first-run suggestions never replace something the person already chose.
  const detect = (onlyDefaults = false) => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const [lang, region] = (navigator.language || "en").split("-");
    const country = ZONES[zone] || (region || "").toUpperCase();
    // A zone named after one city stands for a whole country in India (and is wrong for most of it).
    const city = zone.includes("/") && !["Asia/Kolkata", "Asia/Calcutta"].includes(zone) ? zone.split("/").pop().replace(/_/g, " ") : "";
    const put = (name, value) => { if (value && !(onlyDefaults && fieldState(name).explicit)) setValue(name, value); };
    put("home_location", city);
    put("home_country", country);
    put("home_currency", CURRENCY[country]);
    const languageRow = rows.find(r => r.dataset.field === "search_language");
    if (languageRow) {
      const options = [...$("select", languageRow).options].map(o => o.value);
      put("search_language", [`${lang}-${country}`, lang].find(v => options.includes(v)));
    }
    refreshDirty();
    if (!onlyDefaults) toast(city ? `Filled in from your time zone (${zone}).` : "Filled in from your time zone and language.");
  };
  $$("[data-detect]").forEach(button => button.addEventListener("click", () => detect()));

  // ----------------------------------------------------------- system actions
  for (const button of $$("[data-open]")) {
    button.addEventListener("click", async () => {
      try { await post("/api/settings/open", { target: button.dataset.open }); }
      catch (error) { toast(error.message, true); }
    });
  }
  const clear = $("[data-clear]");
  let confirmTimer = 0;
  clear?.addEventListener("click", async () => {
    if (!clear.classList.contains("is-confirming")) {
      clear.classList.add("is-confirming");
      clear.textContent = "Clear all?";
      confirmTimer = setTimeout(() => { clear.classList.remove("is-confirming"); clear.textContent = "Clear"; }, 3500);
      return;
    }
    clearTimeout(confirmTimer);
    clear.classList.remove("is-confirming");
    clear.textContent = "Clear";
    try {
      await post("/api/settings/conversations/clear");
      $("[data-conversations]").textContent = "0";
      toast("AI conversations cleared.");
    } catch (error) { toast(error.message, true); }
  });

  const historyClear = $("[data-history-clear-all]");
  let historyTimer = 0;
  historyClear?.addEventListener("click", async () => {
    if (!historyClear.classList.contains("is-confirming")) {
      historyClear.classList.add("is-confirming");
      historyClear.textContent = "Clear all?";
      historyTimer = setTimeout(() => { historyClear.classList.remove("is-confirming"); historyClear.textContent = "Clear"; }, 3500);
      return;
    }
    clearTimeout(historyTimer);
    historyClear.classList.remove("is-confirming");
    historyClear.textContent = "Clear";
    try {
      await post("/api/history/clear", { range: "all" });
      toast("Search history cleared. Site preferences are kept.");
    } catch (error) { toast(error.message, true); }
  });

  const overlay = $("[data-overlay]");
  const waitForArcher = async url => {
    const target = new URL(url, location.href);
    const deadline = Date.now() + 25000;
    await new Promise(resolve => setTimeout(resolve, 1200));
    if (target.origin !== location.origin) {
      setTimeout(() => { location.href = target.href; }, 2200);
      return;
    }
    while (Date.now() < deadline) {
      try {
        const response = await fetch("/health", { cache: "no-store" });
        if (response.ok) { location.reload(); return; }
      } catch { /* still restarting */ }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    $("[data-overlay-text]").textContent = "Archer is taking longer than usual. Start it from the Start menu if it doesn't come back.";
  };
  for (const button of $$("[data-restart]")) {
    button.addEventListener("click", async () => {
      if (rows.some(isDirty) && !confirm("Restart without saving your changes?")) return;
      try {
        const { body } = await post("/api/settings/restart");
        overlay.hidden = false;
        waitForArcher(body.url);
      } catch (error) { toast(error.message, true); }
    });
  }

  // ------------------------------------------------------------ state display
  // Home's status rows and the tabs' warning dots. tone: "ok", "warn" or "" (neutral).
  function renderTiles() {
    const p = state.providers;
    const paint = (name, title, text, badge, tone) => {
      const tile = $(`[data-tile="${name}"]`);
      if (tile) {
        $("[data-tile-title]", tile).textContent = title;
        $("[data-tile-text]", tile).textContent = text;
        const chip = $("[data-tile-badge]", tile);
        chip.textContent = badge;
        chip.className = "c-state" + (tone ? " is-" + tone : "");
      }
      const dot = $(`[data-nav-dot="${name}"]`);
      if (dot) dot.className = "c-nav-dot" + (tone === "warn" ? " is-warn" : "");
    };
    paint("search", p.search.label,
      !p.search.ready ? p.search.missing : p.search.fallback && p.search.id !== "duckduckgo" ? "Falls back to DuckDuckGo if it fails" : "Ready",
      p.search.ready ? "Ready" : "Needs setup", p.search.ready ? "ok" : "warn");
    paint("ai", p.ai.label + (p.ai.model ? " · " + p.ai.model : ""),
      !p.ai.ready ? p.ai.missing : !state.ai.consent ? "Ready · turned off until you enable it"
        : state.ai.mode === "balanced" ? "Balanced answers on" : "Answers when you ask",
      !p.ai.ready ? "Needs setup" : state.ai.consent ? "On" : "Off", !p.ai.ready ? "warn" : state.ai.consent ? "ok" : "");
    const on = Boolean(state.autostart?.enabled);
    paint("startup", on ? "Starts with Windows" : "Starts when you open it",
      on ? "Search works as soon as you sign in" : "Turn on Start with Windows so searches always work", on ? "On" : "Off", on ? "ok" : "");
  }
  function renderState() {
    syncAI();
    renderCardStatus();
    renderTiles();
    const usage = state.usage || {};
    for (const box of $$("[data-usage]")) {
      const note = $("[data-usage-note]", box);
      if (usage.error) { note.textContent = usage.error; continue; }
      const usd = Number(usage.usd || 0), tokens = Number(usage.tokens || 0);
      $("[data-usage-usd]", box).replaceChildren(document.createTextNode(usage.priced ? "$" + usd.toFixed(usd < 1 ? 4 : 2) : "—"),
        el("small", { textContent: " of $" + Number(usage.usd_limit).toFixed(2) }));
      $("[data-usage-tokens]", box).replaceChildren(document.createTextNode(tokens.toLocaleString()),
        el("small", { textContent: " of " + Number(usage.token_limit).toLocaleString() }));
      const bar = (selector, share) => {
        const fill = $(selector, box);
        fill.style.width = Math.min(100, Math.max(share > 0 ? 1.5 : 0, share * 100)) + "%";
        fill.classList.toggle("is-high", share >= .8);
      };
      bar("[data-usage-usd-bar]", usage.priced ? usd / usage.usd_limit : 0);
      bar("[data-usage-tokens-bar]", tokens / usage.token_limit);
      note.textContent = (usage.priced ? "" : "This model's price is unknown, so only the token cap applies. ") + "Totals reset at 00:00 UTC.";
    }
    const banner = $("[data-restart-banner]");
    if (banner) {
      banner.hidden = !state.system.restart_pending;
      $("[data-saved-url]").textContent = state.system.saved_url.replace("http://", "");
      $$("[data-restart]").forEach(b => { b.disabled = !state.system.restart_available; });
      if (!state.system.restart_available) banner.querySelector("p").lastChild.textContent = ". Quit Archer from the tray and start it again.";
    }
    const conversations = $("[data-conversations]");
    if (conversations) conversations.textContent = String(state.system.conversations);
  }

  renderModelHints();
  renderState();
  applyWhen();
  if (isSetup) setupWizard(); else appNavigation();

  // ------------------------------------------------------------- app: panes
  function appNavigation() {
    const links = $$("[data-nav]");
    const panes = $$("[data-pane]");
    const toolbar = $(".c-toolbar");
    const show = () => {
      const id = (location.hash || "#overview").slice(1).split("/")[0];
      const pane = panes.find(p => p.dataset.pane === id) || panes[0];
      panes.forEach(p => { p.hidden = p !== pane; });
      links.forEach(link => link.setAttribute("aria-current", String(link.dataset.nav === pane.dataset.pane)));
      scrollTo(0, 0);
    };
    addEventListener("hashchange", show);
    addEventListener("scroll", () => toolbar?.classList.toggle("is-scrolled", scrollY > 4), { passive: true });
    show();
  }

  // ------------------------------------------------------------ setup wizard
  function setupWizard() {
    const steps = $$("[data-step]");
    const order = steps.map(step => step.dataset.step);
    const next = $("[data-next]"), back = $("[data-back]"), status = $("[data-step-status]");
    const noAI = $("[data-no-ai]");
    let index = 0;

    const say = (text, bad = false) => { status.textContent = text || ""; status.classList.toggle("is-bad", bad); };
    const stepRows = step => rows.filter(row => step.contains(row));
    const finished = () => post("/api/app/onboarded", { enabled: true }).catch(() => {});

    const summary = () => {
      const p = state.providers;
      const items = [
        ["Search", p.search.label + (p.search.fallback && p.search.id !== "duckduckgo" ? ", with DuckDuckGo as backup" : "")],
        ["AI", !state.ai.consent ? "Off. Turn it on any time in the companion app." : `${p.ai.label}${p.ai.model ? " · " + p.ai.model : ""}, ${state.ai.mode === "balanced" ? "answering automatically" : "when you ask"}`],
        ["Startup", state.autostart?.enabled ? "Starts with Windows" : "Starts when you open it"],
      ];
      $("[data-summary]").replaceChildren(...items.map(([label, text]) => el("li", {}, el("b", { textContent: label }), el("span", { textContent: text }))));
    };

    const show = i => {
      index = Math.max(0, Math.min(order.length - 1, i));
      steps.forEach((step, n) => { step.hidden = n !== index; });
      $$("[data-step-dot]").forEach((dot, n) => {
        dot.classList.toggle("is-done", n < index);
        dot.classList.toggle("is-current", n === index);
      });
      const key = order[index];
      back.hidden = index === 0 || key === "done";
      $("[data-wizard-nav]").hidden = key === "done";
      next.firstChild.textContent = key === "hello" ? "Get started" : key === "browser" ? "Finish" : "Continue";
      say("");
      applyWhen();
      enter(key);
      scrollTo(0, 0);
    };

    // Suggestions for a first run: only for settings that are still Archer's defaults.
    const enter = async key => {
      if (key === "search" && !enter.searchChecked) {
        enter.searchChecked = true;
        const url = fieldState("searxng_url").value;
        try {
          const { body } = await post("/api/settings/test", { target: "searxng", value: url });
          if (body.ok) {
            $("[data-searxng-found]").hidden = false;
            $("[data-searxng-url]").textContent = url;
          }
          if (!fieldState("search_provider").explicit) setValue("search_provider", body.ok ? "searxng" : "duckduckgo");
        } catch {
          if (!fieldState("search_provider").explicit) setValue("search_provider", "duckduckgo");
        }
        refreshDirty();
      }
      if (key === "ai" && current("ai_provider") === "ollama") {
        const button = $("[data-step='ai'] [data-load-models]");
        if (button && !button.closest("[hidden]")) runTest(button, true);
      }
      if (key === "personal" && !enter.personalChecked) { enter.personalChecked = true; detect(true); }
      if (key === "done") { summary(); finished(); }
    };

    const validate = key => {
      if (key === "search") {
        const provider = current("search_provider");
        const need = provider === "brave" ? ["brave_api_key"] : provider === "google"
          ? (current("google_backend") === "cse" ? ["google_api_key"] : ["serper_api_key"]) : [];
        for (const name of need) {
          if (!fieldState(name).set && !typedSecret(name)) {
            const row = rows.find(r => r.dataset.field === name && !r.closest("[hidden]"));
            if (row) { setError(row, "Paste your key, or choose DuckDuckGo to start without one."); openSecret(row); }
            return false;
          }
        }
        if (provider === "google" && current("google_backend") === "cse" && !visibleInput("google_cx")) {
          const row = rows.find(r => r.dataset.field === "google_cx" && !r.closest("[hidden]"));
          if (row) setError(row, "Add the search engine ID too.");
          return false;
        }
      }
      if (key === "ai" && !noAI.checked) {
        const provider = current("ai_provider");
        const keyName = aiDefaults(provider).key;
        if (keyName && provider !== "custom" && !fieldState(keyName).set && !typedSecret(keyName)) {
          const row = rows.find(r => r.dataset.field === keyName && !r.closest("[hidden]"));
          if (row) { setError(row, "Paste your key, or tick “Skip AI for now”."); openSecret(row); }
          return false;
        }
        if ((provider === "ollama" || provider === "custom") && !visibleInput("ai_model")) {
          const row = rows.find(r => r.dataset.kind === "model");
          if (row) setError(row, provider === "ollama" ? "Load models and pick one. Is Ollama running?" : "Enter the model name your server uses.");
          return false;
        }
        if (provider === "custom" && !visibleInput("ai_base_url")) {
          const row = rows.find(r => r.dataset.field === "ai_base_url" && !r.closest("[hidden]"));
          if (row) setError(row, "Enter your server's address.");
          return false;
        }
      }
      return true;
    };

    const advance = async () => {
      const key = order[index];
      const step = steps[index];
      if (!validate(key)) { say("Something needs a look before you continue.", true); return; }
      next.disabled = true;
      say("Saving…");
      try {
        const subset = stepRows(step).filter(row => key !== "ai" || !noAI.checked || row.dataset.field === "ai_provider");
        if (!(await saveRows(subset, true))) { say("Something needs a look before you continue.", true); return; }
        if (key === "ai") {
          const mode = $$('input[name="ai-mode"]', step).find(input => input.checked)?.value || "classic";
          await savePreferences(!noAI.checked, mode, true);
        }
        if (key === "startup") {
          const box = $("[data-autostart-setup]");
          if (box && box.checked !== Boolean(state.autostart?.enabled)) await setAutostart(box.checked);
        }
        show(index + 1);
      } catch (error) {
        say(error.message, true);
      } finally { next.disabled = false; }
    };

    next.addEventListener("click", advance);
    back.addEventListener("click", () => show(index - 1));
    $("[data-skip]").addEventListener("click", async event => {
      event.preventDefault();
      await finished();
      location.href = "/app";
    });
    noAI.addEventListener("change", () => {
      $("[data-ai-fields]").classList.toggle("is-off", noAI.checked);
      $("[data-step='ai'] .c-cards").classList.toggle("is-off", noAI.checked);
    });
    noAI.checked = !state.ai.consent && fieldState("search_provider").explicit && state.app.onboarded;
    noAI.dispatchEvent(new Event("change"));
    document.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.target.closest("textarea, button, a") && !$("[data-wizard-nav]").hidden) {
        event.preventDefault();
        advance();
      }
    });
    show(0);
  }
});
