// Archer Settings. Typed fields collect in a save bar; switches and segments apply at once.
// Keys are never sent back to the page: it only knows whether one is set and its last four characters.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let state = JSON.parse($("#settings-state").textContent);
  const csrf = document.body.dataset.csrf;
  const rows = $$("[data-field]");
  const savebar = $("[data-savebar]");
  const toastBox = $("[data-toast]");
  let toastTimer = 0;

  const toast = (text, bad = false) => {
    toastBox.textContent = text;
    toastBox.classList.toggle("is-bad", bad);
    toastBox.classList.add("is-shown");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastBox.classList.remove("is-shown"), bad ? 5000 : 2600);
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
  const fieldState = name => state.fields.find(f => f.name === name) || {};

  // ------------------------------------------------------------ typed fields
  const valueOf = row => {
    const input = $("[data-input]", row);
    if (row.dataset.kind === "secret") {
      if (row.classList.contains("is-removing")) return "";
      return input.value.trim() || undefined;
    }
    return input.value.trim();
  };
  const dirtyRows = () => rows.filter(row => {
    if (row.hasAttribute("data-locked")) return false;
    const value = valueOf(row);
    if (row.dataset.kind === "secret") return value !== undefined;
    return value !== (fieldState(row.dataset.field).value ?? "");
  });
  const refreshDirty = () => {
    const dirty = dirtyRows();
    rows.forEach(row => row.classList.toggle("is-dirty", dirty.includes(row)));
    $("[data-dirty-count]").textContent = dirty.length === 1 ? "1 change" : dirty.length + " changes";
    savebar.hidden = !dirty.length;
  };
  const setError = (row, message) => {
    const error = $("[data-error]", row);
    error.textContent = message || "";
    error.hidden = !message;
    row.classList.toggle("has-error", Boolean(message));
  };
  const renderSecret = row => {
    const item = fieldState(row.dataset.field);
    const box = $("[data-secret]", row);
    box.dataset.set = String(Boolean(item.set));
    box.classList.remove("is-editing", "is-removing");
    row.classList.remove("is-removing");
    $("[data-secret-hint]", row).textContent = item.set ? item.hint : "Not set";
    $(".set-secret-entry", row).hidden = true;
    $("[data-input]", row).value = "";
    const edit = $("[data-secret-edit]", row), remove = $("[data-secret-remove]", row);
    if (edit) edit.textContent = item.set ? "Replace" : "Add key";
    if (remove) { remove.hidden = !item.set; remove.textContent = "Remove"; }
  };
  const discard = () => {
    for (const row of rows) {
      setError(row, "");
      if (row.dataset.kind === "secret") renderSecret(row);
      else $("[data-input]", row).value = fieldState(row.dataset.field).value ?? "";
    }
    refreshDirty();
  };

  for (const row of rows) {
    const input = $("[data-input]", row);
    input.addEventListener("input", () => { setError(row, ""); refreshDirty(); });
    input.addEventListener("change", refreshDirty);
    if (row.dataset.kind !== "secret") continue;
    const box = $("[data-secret]", row);
    $("[data-secret-edit]", row)?.addEventListener("click", () => {
      if (box.classList.contains("is-editing")) {
        renderSecret(row);
        setError(row, "");
        refreshDirty();
        return;
      }
      row.classList.remove("is-removing");
      box.classList.remove("is-removing");
      box.classList.add("is-editing");
      $(".set-secret-entry", row).hidden = false;
      $("[data-secret-edit]", row).textContent = "Cancel";
      const remove = $("[data-secret-remove]", row);
      if (remove) remove.hidden = true;
      input.focus();
    });
    $("[data-secret-remove]", row)?.addEventListener("click", () => {
      const removing = !row.classList.contains("is-removing");
      row.classList.toggle("is-removing", removing);
      box.classList.toggle("is-removing", removing);
      $("[data-secret-remove]", row).textContent = removing ? "Keep" : "Remove";
      refreshDirty();
    });
    $("[data-reveal]", row).addEventListener("click", event => {
      const shown = input.type === "text";
      input.type = shown ? "password" : "text";
      event.currentTarget.setAttribute("aria-label", shown ? "Show key" : "Hide key");
    });
  }

  const save = async () => {
    const dirty = dirtyRows();
    if (!dirty.length) return;
    const values = Object.fromEntries(dirty.map(row => [row.dataset.field, valueOf(row)]));
    const button = $("[data-save]");
    button.disabled = true;
    try {
      const { status, body } = await post("/api/settings", { values });
      if (status === 422) {
        let first = null;
        for (const [name, message] of Object.entries(body.errors || {})) {
          const row = rows.find(r => r.dataset.field === name);
          if (row) { setError(row, message); first ||= row; }
        }
        first?.scrollIntoView({ block: "center", behavior: "smooth" });
        toast("Some settings need a look before they can be saved.", true);
        return;
      }
      state = body.state;
      rows.forEach(row => setError(row, ""));
      discard();
      renderState();
      toast(body.restart ? "Saved. Restart Archer to apply the new address." : body.changed.length ? "Saved and applied." : "Saved.");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  };
  $("[data-save]").addEventListener("click", save);
  $("[data-discard]").addEventListener("click", discard);
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
  });
  addEventListener("beforeunload", event => { if (dirtyRows().length) event.preventDefault(); });

  // ------------------------------------------------------------ connection tests
  for (const button of $$("[data-test]")) {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-field]");
      const result = $("[data-test-result]", row);
      const typed = $("[data-input]", row).value.trim();
      const base = $('[data-field="ai_base_url"] [data-input]')?.value.trim() || "";
      button.disabled = true;
      result.className = "set-result";
      result.textContent = "Checking…";
      try {
        const { body } = await post("/api/settings/test", { target: button.dataset.test, value: typed, base: button.dataset.test === "ai" ? base : "" });
        result.textContent = body.message;
        result.classList.add(body.ok ? "is-ok" : "is-bad");
        if (body.models?.length) {
          const list = $("#list-ai_model");
          list.replaceChildren(...body.models.map(model => Object.assign(document.createElement("option"), { value: model })));
        }
      } catch (error) {
        result.textContent = error.message;
        result.classList.add("is-bad");
      } finally { button.disabled = false; }
    });
  }

  // ------------------------------------------------------------ instant switches
  const consent = $("[data-ai-consent]");
  const modeInputs = $$('input[name="ai-mode"]');
  const syncAI = () => {
    consent.checked = state.ai.consent;
    modeInputs.forEach(input => { input.checked = input.value === state.ai.mode; input.disabled = !state.ai.consent; });
  };
  const savePreferences = async (nextConsent, nextMode) => {
    try {
      const { body } = await post("/api/ai/preferences", { consent: nextConsent, mode: nextConsent ? nextMode : "classic" });
      state.ai.consent = body.consent;
      state.ai.mode = body.default_mode;
      toast(!body.consent ? "Cloud AI is off. Nothing will be sent." : body.default_mode === "balanced" ? "Balanced answers will start automatically." : "Saved. AI starts only when you ask.");
    } catch (error) { toast(error.message, true); }
    syncAI();
  };
  consent.addEventListener("change", () => savePreferences(consent.checked, state.ai.mode));
  modeInputs.forEach(input => input.addEventListener("change", () => savePreferences(true, input.value)));

  for (const box of $$("[data-local]")) {
    const key = box.dataset.local;
    try { box.checked = (localStorage.getItem(key) ?? box.dataset.localDefault) === "1"; } catch { box.disabled = true; }
    box.addEventListener("change", () => {
      try { localStorage.setItem(key, box.checked ? "1" : "0"); toast("Saved in this browser."); }
      catch { toast("This browser isn't keeping site data.", true); }
    });
  }

  const autostart = $("[data-autostart]");
  autostart?.addEventListener("change", async () => {
    try {
      const { body } = await post("/api/settings/autostart", { enabled: autostart.checked });
      autostart.checked = body.enabled;
      toast(body.enabled ? "Archer will start when you sign in." : "Archer won't start automatically.");
    } catch (error) { autostart.checked = !autostart.checked; toast(error.message, true); }
  });

  // ------------------------------------------------------------ system actions
  for (const button of $$("[data-open]")) {
    button.addEventListener("click", async () => {
      try { await post("/api/settings/open", { target: button.dataset.open }); }
      catch (error) { toast(error.message, true); }
    });
  }
  const clear = $("[data-clear]");
  let confirmTimer = 0;
  clear.addEventListener("click", async () => {
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

  const overlay = $("[data-overlay]");
  const waitForArcher = async url => {
    const target = new URL(url, location.href);
    const deadline = Date.now() + 25000;
    await new Promise(resolve => setTimeout(resolve, 1200));
    if (target.origin !== location.origin) {
      // A new address can't be polled from this page; give the restart a moment, then go there.
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
      if (dirtyRows().length && !confirm("Restart without saving your changes?")) return;
      try {
        const { body } = await post("/api/settings/restart");
        overlay.hidden = false;
        $("[data-overlay-text]").textContent = "Restarting Archer…";
        waitForArcher(body.url);
      } catch (error) { toast(error.message, true); }
    });
  }

  // ------------------------------------------------------------ copy fields
  for (const button of $$("[data-copy]")) {
    button.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(button.dataset.copy); button.textContent = "Copied"; }
      catch { button.textContent = "Select and copy"; }
      setTimeout(() => { button.textContent = "Copy"; }, 1500);
    });
  }

  // ------------------------------------------------------------ state-driven pieces
  function renderState() {
    syncAI();
    const usage = state.usage || {};
    const note = $("[data-usage-note]");
    if (usage.error) {
      note.textContent = usage.error;
    } else {
      const usd = Number(usage.usd || 0), tokens = Number(usage.tokens || 0);
      $("[data-usage-usd]").replaceChildren(document.createTextNode(usage.priced ? "$" + usd.toFixed(usd < 1 ? 4 : 2) : "—"),
        Object.assign(document.createElement("small"), { textContent: " of $" + Number(usage.usd_limit).toFixed(2) }));
      $("[data-usage-tokens]").replaceChildren(document.createTextNode(tokens.toLocaleString()),
        Object.assign(document.createElement("small"), { textContent: " of " + Number(usage.token_limit).toLocaleString() }));
      const bar = (selector, share) => {
        const fill = $(selector);
        fill.style.width = Math.min(100, Math.max(share > 0 ? 1.5 : 0, share * 100)) + "%";
        fill.classList.toggle("is-high", share >= .8);
      };
      bar("[data-usage-usd-bar]", usage.priced ? usd / usage.usd_limit : 0);
      bar("[data-usage-tokens-bar]", tokens / usage.token_limit);
      note.textContent = (usage.priced ? "" : "Pricing for this model is unknown, so only the token cap applies. ") + "Totals reset at 00:00 UTC.";
    }
    const banner = $("[data-restart-banner]");
    banner.hidden = !state.system.restart_pending;
    $("[data-saved-url]").textContent = state.system.saved_url.replace("http://", "");
    $$("[data-restart]").forEach(b => { b.disabled = !state.system.restart_available; });
    if (!state.system.restart_available) banner.querySelector("p").lastChild.textContent = ". Quit Archer from the tray and start it again.";
    $("[data-conversations]").textContent = String(state.system.conversations);
  }
  renderState();

  // ------------------------------------------------------------ navigation
  const links = $$("[data-nav]");
  const sections = links.map(link => document.getElementById(link.dataset.nav)).filter(Boolean);
  const mark = id => links.forEach(link => link.setAttribute("aria-current", String(link.dataset.nav === id)));
  const spy = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (visible) mark(visible.target.id);
  }, { rootMargin: "-10% 0px -70% 0px" });
  sections.forEach(section => spy.observe(section));
  links.forEach(link => link.addEventListener("click", () => mark(link.dataset.nav)));
  mark((location.hash || "#general").slice(1));
});
