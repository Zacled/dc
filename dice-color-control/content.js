/* Dice Color Control — content script (onlinedice.org color dice)
 *
 * Two jobs, both purely visual (local display override, this tab only):
 *   - BLOCK a color  -> that color is never shown; the die is recolored to an
 *                       allowed color instead.
 *   - FORCE a color  -> every die is shown in that color.
 *
 * The dice are auto-detected (colored, square-ish elements sitting in a row),
 * so there is nothing to pick. Colors are classified by hue into the site's
 * six names, and the site's real shade for each color is learned automatically
 * so replacements look native.
 */
(() => {
  "use strict";

  const STORE_KEY = "dcc";

  const COLOR_NAMES = ["Red", "Orange", "Yellow", "Green", "Blue", "Purple"];

  // Defaults; overwritten by the site's real shades as we observe them.
  const DEFAULT_HEX = {
    Red: "#e23b3b",
    Orange: "#ef8a2b",
    Yellow: "#f4c531",
    Green: "#34a23f",
    Blue: "#3f6fd1",
    Purple: "#8e44c9"
  };

  const DEFAULT_CFG = {
    enabled: true,
    blocked: [], // color names that must never appear
    forced: "", // "" = off, otherwise a color name shown on every die
    manualSelector: "" // optional fallback if auto-detect misses the dice
  };

  let cfg = Object.assign({}, DEFAULT_CFG);
  const learnedHex = Object.assign({}, DEFAULT_HEX);

  let applying = false; // true while WE write, so our writes aren't read as a roll
  let rafPending = 0;

  // ---- config sync ----------------------------------------------------------

  function loadCfg() {
    chrome.storage.local.get(STORE_KEY, (res) => {
      cfg = Object.assign({}, DEFAULT_CFG, res && res[STORE_KEY]);
      schedule();
    });
  }
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes[STORE_KEY]) return;
    cfg = Object.assign({}, DEFAULT_CFG, changes[STORE_KEY].newValue);
    schedule();
  });

  // ---- color math -----------------------------------------------------------

  function parseColor(str) {
    if (!str) return null;
    const m = str.match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const p = m[1].split(",").map((x) => parseFloat(x.trim()));
    const a = p.length > 3 ? p[3] : 1;
    return { r: p[0], g: p[1], b: p[2], a };
  }

  function rgbToHsl({ r, g, b }) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h = 0;
    const l = (max + min) / 2;
    const d = max - min;
    const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
    if (d !== 0) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h, s, l };
  }

  // Map an rgb color to one of the six site color names (or null if greyish).
  function classify(rgb) {
    if (!rgb || rgb.a < 0.4) return null;
    const { h, s, l } = rgbToHsl(rgb);
    if (s < 0.2 || l > 0.92 || l < 0.08) return null; // grey/white/black -> not a die color
    if (h >= 345 || h < 18) return "Red";
    if (h < 42) return "Orange";
    if (h < 70) return "Yellow";
    if (h < 165) return "Green";
    if (h < 255) return "Blue";
    return "Purple";
  }

  function colorValue(name) {
    return learnedHex[name] || DEFAULT_HEX[name] || "#888";
  }

  // ---- find the dice --------------------------------------------------------

  function isDie(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 50 || r.height < 50 || r.width > 280 || r.height > 280) return false;
    const ar = r.width / r.height;
    if (ar < 0.7 || ar > 1.4) return false; // square-ish
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) return false;
    const rgb = parseColor(cs.backgroundColor);
    if (!rgb || rgb.a < 0.5) return false;
    return classify(rgb) !== null; // vivid, one of the six colors
  }

  function resolve(sel) {
    if (!sel) return null;
    try { return document.querySelector(sel); } catch { return null; }
  }

  function findDice() {
    // Manual fallback scope, if the user picked one.
    const manual = resolve(cfg.manualSelector);
    const scope = manual ? (isDie(manual) ? manual.parentElement : manual) : document;

    let cands = Array.prototype.filter.call(
      scope.querySelectorAll("div,span,button,li,a,i,td"),
      isDie
    );
    // Drop any candidate that contains another candidate (keep innermost colored box).
    cands = cands.filter((el) => !cands.some((o) => o !== el && el.contains(o)));
    if (cands.length <= 1) return cands;

    // Cluster into a single horizontal row of similar-sized boxes (the dice).
    const items = cands.map((el) => ({ el, r: el.getBoundingClientRect() }));
    let best = [];
    for (const seed of items) {
      const cluster = items.filter(
        (o) =>
          Math.abs(o.r.top - seed.r.top) < 24 &&
          o.r.height > seed.r.height * 0.6 &&
          o.r.height < seed.r.height * 1.6
      );
      if (cluster.length > best.length) best = cluster;
    }
    best.sort((a, b) => a.r.left - b.r.left);
    return best.map((o) => o.el);
  }

  // ---- apply ----------------------------------------------------------------

  function apply() {
    if (!cfg || !cfg.enabled) {
      clearAll();
      return;
    }
    const dice = findDice();
    if (!dice.length) return;

    // 1) Read + learn the site's natural colors BEFORE we overwrite anything.
    const natural = dice.map((d) => {
      const rgb = parseColor(getComputedStyle(d).backgroundColor);
      const name = classify(rgb);
      if (name && rgb) learnedHex[name] = `rgb(${Math.round(rgb.r)}, ${Math.round(rgb.g)}, ${Math.round(rgb.b)})`;
      return name;
    });

    const allowed = COLOR_NAMES.filter((n) => !cfg.blocked.includes(n));

    applying = true;
    dice.forEach((d, i) => {
      let want = null;
      if (cfg.forced) {
        want = cfg.forced; // force this color on every die
      } else if (natural[i] && cfg.blocked.includes(natural[i])) {
        want = allowed.length ? allowed[i % allowed.length] : natural[i]; // swap blocked color out
      }
      if (want) {
        d.style.setProperty("background-color", colorValue(want), "important");
        d.setAttribute("data-dcc", "1");
      } else if (d.getAttribute("data-dcc") === "1") {
        d.style.removeProperty("background-color");
        d.removeAttribute("data-dcc");
      }
    });
    requestAnimationFrame(() => {
      applying = false;
    });
  }

  function clearAll() {
    const marked = document.querySelectorAll('[data-dcc="1"]');
    if (!marked.length) return;
    applying = true;
    marked.forEach((d) => {
      d.style.removeProperty("background-color");
      d.removeAttribute("data-dcc");
    });
    requestAnimationFrame(() => {
      applying = false;
    });
  }

  function schedule() {
    if (rafPending) return;
    rafPending = requestAnimationFrame(() => {
      rafPending = 0;
      apply();
    });
  }

  // ---- observe rolls --------------------------------------------------------

  const observer = new MutationObserver(() => {
    if (applying) return; // ignore our own writes
    schedule();
  });

  function startObserving() {
    const root = document.documentElement || document.body;
    if (!root) return void requestAnimationFrame(startObserving);
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["style", "class", "fill"],
      characterData: true
    });
  }

  // ---- optional manual picker (fallback) ------------------------------------

  let pickOverlay = null;
  function ensureOverlay() {
    if (pickOverlay) return pickOverlay;
    const o = document.createElement("div");
    o.style.cssText =
      "position:fixed;z-index:2147483647;pointer-events:none;border:2px solid #2563eb;" +
      "background:rgba(37,99,235,.15);border-radius:6px;display:none;";
    const tip = document.createElement("div");
    tip.style.cssText =
      "position:fixed;z-index:2147483647;top:8px;left:50%;transform:translateX(-50%);" +
      "background:#111827;color:#fff;font:600 13px system-ui;padding:8px 12px;border-radius:8px;" +
      "pointer-events:none;display:none;";
    o._tip = tip;
    document.documentElement.append(o, tip);
    pickOverlay = o;
    return o;
  }
  function cssPath(el) {
    if (el.id) {
      const s = `#${CSS.escape(el.id)}`;
      try { if (document.querySelectorAll(s).length === 1) return s; } catch {}
    }
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && n !== document.documentElement) {
      let p = n.nodeName.toLowerCase();
      const cls = typeof n.className === "string" ? n.className.trim().split(/\s+/).filter(Boolean).slice(0, 2) : [];
      if (cls.length) p += "." + cls.map((c) => CSS.escape(c)).join(".");
      const sibs = n.parentNode ? Array.prototype.filter.call(n.parentNode.children, (c) => c.nodeName === n.nodeName) : [];
      if (sibs.length > 1) p += `:nth-of-type(${1 + sibs.indexOf(n)})`;
      parts.unshift(p);
      n = n.parentNode;
    }
    return parts.join(" > ");
  }
  function pmove(e) {
    const el = e.target;
    if (!(el instanceof Element)) return;
    const r = el.getBoundingClientRect();
    const o = ensureOverlay();
    o.style.display = "block";
    Object.assign(o.style, { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    o._tip.style.display = "block";
    o._tip.textContent = "Click one of the dice  (Esc to cancel)";
  }
  function pclick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!(e.target instanceof Element)) return;
    chrome.storage.local.set({ [STORE_KEY]: Object.assign({}, cfg, { manualSelector: cssPath(e.target) }) });
    stopPick();
  }
  function pkey(e) { if (e.key === "Escape") stopPick(); }
  function startPick() {
    ensureOverlay();
    document.addEventListener("mousemove", pmove, true);
    document.addEventListener("click", pclick, true);
    document.addEventListener("keydown", pkey, true);
  }
  function stopPick() {
    document.removeEventListener("mousemove", pmove, true);
    document.removeEventListener("click", pclick, true);
    document.removeEventListener("keydown", pkey, true);
    if (pickOverlay) { pickOverlay.style.display = "none"; pickOverlay._tip.style.display = "none"; }
  }

  chrome.runtime.onMessage.addListener((msg, _s, send) => {
    if (!msg || !msg.type) return;
    if (msg.type === "ping") {
      const n = findDice().length;
      send({ ok: true, dice: n });
      return true;
    }
    if (msg.type === "pick") { startPick(); send({ ok: true }); return true; }
    if (msg.type === "applyNow") { apply(); send({ ok: true }); return true; }
  });

  // ---- go -------------------------------------------------------------------

  loadCfg();
  startObserving();
})();
