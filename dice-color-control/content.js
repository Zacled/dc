/* Dice Color Control — content script
 *
 * Runs on onlinedice.org. Its job:
 *   1. Let the user click the on-page element that shows the result color
 *      (the "color box"), and remember a CSS selector for it.
 *   2. After every roll, overwrite that element's color with the color the
 *      user chose in the popup ("force" mode), optionally cycling through a
 *      sequence of colors and/or overwriting a text label.
 *   3. Optionally "learn" the exact colors the site naturally shows so the
 *      user can reuse them.
 *
 * Everything here is purely a local display override — it changes what this
 * browser paints, nothing leaves the machine.
 */
(() => {
  "use strict";

  const STORE_KEY = "dcc";

  /** Current config, kept in sync with chrome.storage. */
  let cfg = null;

  /** In-memory sequence cursor (not persisted, to avoid storage feedback loops). */
  let seqIndex = 0;

  /** True while WE are mutating the DOM, so our own changes don't look like a roll. */
  let applying = false;

  /** Coalesce bursts of mutations into one logical "roll". */
  let rafPending = 0;

  // ---- config load / sync ---------------------------------------------------

  const DEFAULT_CFG = {
    enabled: false,
    mode: "fixed", // "fixed" | "sequence"
    colorSelector: "", // element whose background we recolor
    labelSelector: "", // optional element whose text we replace with the color name
    forceText: true,
    target: { name: "Red", css: "#e11d48" },
    sequence: [],
    learn: false,
    learned: []
  };

  function loadCfg() {
    chrome.storage.local.get(STORE_KEY, (res) => {
      cfg = Object.assign({}, DEFAULT_CFG, res && res[STORE_KEY]);
      applyForce(); // apply immediately if already enabled
    });
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes[STORE_KEY]) return;
    cfg = Object.assign({}, DEFAULT_CFG, changes[STORE_KEY].newValue);
    applyForce();
  });

  // ---- helpers --------------------------------------------------------------

  function resolve(sel) {
    if (!sel) return null;
    try {
      return document.querySelector(sel);
    } catch {
      return null;
    }
  }

  function pickTarget() {
    if (!cfg) return null;
    if (cfg.mode === "sequence" && Array.isArray(cfg.sequence) && cfg.sequence.length) {
      return cfg.sequence[((seqIndex % cfg.sequence.length) + cfg.sequence.length) % cfg.sequence.length];
    }
    return cfg.target;
  }

  // Build a reasonably stable, unique CSS selector for an element.
  function cssPath(el) {
    if (!(el instanceof Element)) return "";
    if (el.id) {
      const byId = `#${CSS.escape(el.id)}`;
      try {
        if (document.querySelectorAll(byId).length === 1) return byId;
      } catch {}
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      let part = node.nodeName.toLowerCase();
      if (node.id) {
        part = `#${CSS.escape(node.id)}`;
        parts.unshift(part);
        break;
      }
      // a couple of classes for readability/stability
      const cls = (node.className && typeof node.className === "string")
        ? node.className.trim().split(/\s+/).filter(Boolean).slice(0, 2)
        : [];
      if (cls.length) part += "." + cls.map((c) => CSS.escape(c)).join(".");
      // nth-of-type for uniqueness among siblings
      const parent = node.parentNode;
      if (parent) {
        const sameTag = Array.prototype.filter.call(
          parent.children,
          (c) => c.nodeName === node.nodeName
        );
        if (sameTag.length > 1) {
          part += `:nth-of-type(${1 + sameTag.indexOf(node)})`;
        }
      }
      parts.unshift(part);
      node = node.parentNode;
    }
    return parts.join(" > ");
  }

  function naturalColorOf(el) {
    // Prefer an explicit inline background, else computed background-color.
    const cs = getComputedStyle(el);
    const bg = cs.backgroundColor;
    if (bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") return bg;
    // fall back to color (some sites color text instead of a box)
    return cs.color || bg;
  }

  function recordLearned(el) {
    if (!cfg || !cfg.learn || !el) return;
    const css = naturalColorOf(el);
    if (!css) return;
    const exists = cfg.learned.some((l) => l.css === css);
    if (exists) return;
    const labelEl = resolve(cfg.labelSelector);
    const name = labelEl ? (labelEl.textContent || "").trim().slice(0, 24) : "";
    const learned = cfg.learned.concat([{ css, name: name || css }]);
    chrome.storage.local.set({ [STORE_KEY]: Object.assign({}, cfg, { learned }) });
  }

  // ---- core: force the chosen color ----------------------------------------

  function applyForce() {
    if (!cfg || !cfg.enabled) return;
    const el = resolve(cfg.colorSelector);
    if (!el) return;
    const t = pickTarget();
    if (!t || !t.css) return;

    applying = true;
    el.style.setProperty("background-color", t.css, "important");
    el.setAttribute("data-dcc", "1");

    if (cfg.forceText && t.name) {
      const labelEl = resolve(cfg.labelSelector);
      if (labelEl) labelEl.textContent = t.name;
    }
    // Release the guard on the next frame, after our own mutations have been
    // delivered to the observer (microtask) — so they aren't counted as a roll.
    requestAnimationFrame(() => {
      applying = false;
    });
  }

  // A real site re-render (a roll) lands here.
  function onRoll() {
    if (!cfg) return;
    const el = resolve(cfg.colorSelector);
    if (!el) return;

    if (cfg.learn) recordLearned(el);
    if (!cfg.enabled) return;

    applyForce();

    if (cfg.mode === "sequence" && Array.isArray(cfg.sequence) && cfg.sequence.length) {
      seqIndex = (seqIndex + 1) % cfg.sequence.length;
    }
  }

  // ---- observe the page for rolls ------------------------------------------

  const observer = new MutationObserver(() => {
    if (applying) return; // ignore our own writes
    if (rafPending) return;
    rafPending = requestAnimationFrame(() => {
      rafPending = 0;
      onRoll();
    });
  });

  function startObserving() {
    const root = document.documentElement || document.body;
    if (!root) {
      requestAnimationFrame(startObserving);
      return;
    }
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["style", "class", "fill"],
      characterData: true
    });
  }

  // ---- element picker -------------------------------------------------------

  let pickKind = null; // "color" | "label"
  let pickOverlay = null;

  function ensureOverlay() {
    if (pickOverlay) return pickOverlay;
    const o = document.createElement("div");
    o.style.cssText =
      "position:fixed;z-index:2147483647;pointer-events:none;border:2px solid #2563eb;" +
      "background:rgba(37,99,235,.15);box-shadow:0 0 0 2px rgba(255,255,255,.6);border-radius:4px;" +
      "transition:all .03s ease;display:none;";
    const tip = document.createElement("div");
    tip.style.cssText =
      "position:fixed;z-index:2147483647;top:8px;left:50%;transform:translateX(-50%);" +
      "background:#111827;color:#fff;font:600 13px/1.4 system-ui,sans-serif;padding:8px 12px;" +
      "border-radius:8px;pointer-events:none;box-shadow:0 6px 20px rgba(0,0,0,.35);display:none;";
    o._tip = tip;
    document.documentElement.appendChild(o);
    document.documentElement.appendChild(tip);
    pickOverlay = o;
    return o;
  }

  function onPickMove(e) {
    const el = e.target;
    if (!(el instanceof Element)) return;
    const r = el.getBoundingClientRect();
    const o = ensureOverlay();
    o.style.display = "block";
    o.style.left = r.left + "px";
    o.style.top = r.top + "px";
    o.style.width = r.width + "px";
    o.style.height = r.height + "px";
    o._tip.style.display = "block";
    o._tip.textContent =
      pickKind === "label"
        ? "Click the text that names the color  (Esc to cancel)"
        : "Click the color box / die  (Esc to cancel)";
  }

  function onPickClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const el = e.target;
    if (!(el instanceof Element)) return;
    const sel = cssPath(el);
    const patch = pickKind === "label" ? { labelSelector: sel } : { colorSelector: sel };
    chrome.storage.local.set({ [STORE_KEY]: Object.assign({}, cfg, patch) }, () => {
      try {
        chrome.runtime.sendMessage({ type: "picked", kind: pickKind, selector: sel });
      } catch {}
    });
    stopPick();
  }

  function onPickKey(e) {
    if (e.key === "Escape") stopPick();
  }

  function startPick(kind) {
    pickKind = kind;
    ensureOverlay();
    document.addEventListener("mousemove", onPickMove, true);
    document.addEventListener("click", onPickClick, true);
    document.addEventListener("keydown", onPickKey, true);
  }

  function stopPick() {
    pickKind = null;
    document.removeEventListener("mousemove", onPickMove, true);
    document.removeEventListener("click", onPickClick, true);
    document.removeEventListener("keydown", onPickKey, true);
    if (pickOverlay) {
      pickOverlay.style.display = "none";
      pickOverlay._tip.style.display = "none";
    }
  }

  // ---- messaging from popup -------------------------------------------------

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.type) return;
    if (msg.type === "ping") {
      sendResponse({
        ok: true,
        colorSelector: cfg && cfg.colorSelector,
        labelSelector: cfg && cfg.labelSelector
      });
      return true;
    }
    if (msg.type === "pick") {
      startPick(msg.kind === "label" ? "label" : "color");
      sendResponse({ ok: true });
      return true;
    }
    if (msg.type === "applyNow") {
      applyForce();
      sendResponse({ ok: true });
      return true;
    }
  });

  // ---- go -------------------------------------------------------------------

  loadCfg();
  startObserving();
})();
