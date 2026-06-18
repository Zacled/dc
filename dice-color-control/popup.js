/* Dice Color Control — popup UI */
(() => {
  "use strict";

  const STORE_KEY = "dcc";

  const PRESETS = [
    { name: "Red", css: "#e11d48" },
    { name: "Orange", css: "#f97316" },
    { name: "Yellow", css: "#eab308" },
    { name: "Green", css: "#22c55e" },
    { name: "Blue", css: "#3b82f6" },
    { name: "Purple", css: "#a855f7" },
    { name: "Pink", css: "#ec4899" },
    { name: "Cyan", css: "#06b6d4" },
    { name: "Brown", css: "#92400e" },
    { name: "White", css: "#ffffff" },
    { name: "Gray", css: "#6b7280" },
    { name: "Black", css: "#111111" }
  ];

  const DEFAULT_CFG = {
    enabled: false,
    mode: "fixed",
    colorSelector: "",
    labelSelector: "",
    forceText: true,
    target: { name: "Red", css: "#e11d48" },
    sequence: [],
    learn: false,
    learned: []
  };

  let cfg = Object.assign({}, DEFAULT_CFG);
  let tabId = null;

  const $ = (id) => document.getElementById(id);

  function save() {
    chrome.storage.local.set({ [STORE_KEY]: cfg });
  }

  function sameColor(a, b) {
    return a && b && a.css === b.css && a.name === b.name;
  }

  // ---- render ---------------------------------------------------------------

  function render() {
    $("enabled").checked = !!cfg.enabled;
    $("forceText").checked = !!cfg.forceText;
    $("learn").checked = !!cfg.learn;
    $("colorSel").textContent = cfg.colorSelector || "not set";
    $("colorSel").title = cfg.colorSelector || "";
    $("labelSel").textContent = cfg.labelSelector || "optional";
    $("labelSel").title = cfg.labelSelector || "";

    for (const r of document.querySelectorAll('input[name="mode"]')) {
      r.checked = r.value === cfg.mode;
    }
    $("seqBox").hidden = cfg.mode !== "sequence";

    $("currentTarget").textContent = cfg.target ? `${cfg.target.name} (${cfg.target.css})` : "—";

    renderSwatches();
    renderLearned();
    renderSequence();
  }

  function renderSwatches() {
    const box = $("swatches");
    box.innerHTML = "";
    for (const c of PRESETS) {
      const el = document.createElement("div");
      el.className = "swatch" + (sameColor(c, cfg.target) ? " sel-on" : "");
      el.style.background = c.css;
      el.title = `${c.name} ${c.css}`;
      el.addEventListener("click", () => {
        cfg.target = { name: c.name, css: c.css };
        save();
        render();
      });
      box.appendChild(el);
    }
  }

  function renderLearned() {
    const box = $("learned");
    box.innerHTML = "";
    if (!cfg.learned.length) {
      box.innerHTML = '<span class="hint">None yet — enable learning and roll.</span>';
      return;
    }
    for (const c of cfg.learned) {
      const el = document.createElement("div");
      el.className = "swatch" + (sameColor(c, cfg.target) ? " sel-on" : "");
      el.style.background = c.css;
      el.title = `${c.name} ${c.css}`;
      el.addEventListener("click", () => {
        cfg.target = { name: c.name, css: c.css };
        save();
        render();
      });
      box.appendChild(el);
    }
  }

  function renderSequence() {
    const ol = $("seqList");
    ol.innerHTML = "";
    cfg.sequence.forEach((c, i) => {
      const li = document.createElement("li");
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.style.background = c.css;
      const txt = document.createElement("span");
      txt.textContent = `${c.name}`;
      const rm = document.createElement("button");
      rm.textContent = "✕";
      rm.title = "Remove";
      rm.addEventListener("click", () => {
        cfg.sequence.splice(i, 1);
        save();
        render();
      });
      li.append(chip, txt, rm);
      ol.appendChild(li);
    });
  }

  // ---- talk to the page -----------------------------------------------------

  function onActiveTab(cb) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      cb(tab);
    });
  }

  function checkSite() {
    onActiveTab((tab) => {
      const status = $("status");
      const isSite = tab && /^https:\/\/(www\.)?onlinedice\.org\//.test(tab.url || "");
      if (!isSite) {
        status.textContent = "Open onlinedice.org in this tab to use the picker.";
        status.className = "status warn";
        return;
      }
      tabId = tab.id;
      chrome.tabs.sendMessage(tabId, { type: "ping" }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          status.textContent = "Loaded — reload the dice page if Pick does nothing.";
          status.className = "status warn";
        } else {
          status.textContent = "Connected to onlinedice.org.";
          status.className = "status ok";
        }
      });
    });
  }

  function startPick(kind) {
    if (tabId == null) {
      checkSite();
      return;
    }
    chrome.tabs.sendMessage(tabId, { type: "pick", kind }, () => {
      // Close the popup so the user can interact with the page.
      window.close();
    });
  }

  // ---- wire up --------------------------------------------------------------

  function init() {
    chrome.storage.local.get(STORE_KEY, (res) => {
      cfg = Object.assign({}, DEFAULT_CFG, res && res[STORE_KEY]);
      render();
    });
    checkSite();

    $("enabled").addEventListener("change", (e) => {
      cfg.enabled = e.target.checked;
      save();
      if (cfg.enabled && tabId != null) chrome.tabs.sendMessage(tabId, { type: "applyNow" });
    });
    $("forceText").addEventListener("change", (e) => {
      cfg.forceText = e.target.checked;
      save();
    });
    $("learn").addEventListener("change", (e) => {
      cfg.learn = e.target.checked;
      save();
    });

    $("pickColor").addEventListener("click", () => startPick("color"));
    $("pickLabel").addEventListener("click", () => startPick("label"));

    for (const r of document.querySelectorAll('input[name="mode"]')) {
      r.addEventListener("change", (e) => {
        cfg.mode = e.target.value;
        save();
        render();
      });
    }

    $("setCustom").addEventListener("click", () => {
      const css = $("customColor").value;
      const name = ($("customName").value || "").trim() || css;
      cfg.target = { name, css };
      save();
      render();
    });

    $("addSeq").addEventListener("click", () => {
      if (cfg.target) cfg.sequence.push({ name: cfg.target.name, css: cfg.target.css });
      save();
      render();
    });
    $("clearSeq").addEventListener("click", () => {
      cfg.sequence = [];
      save();
      render();
    });

    $("reset").addEventListener("click", () => {
      cfg = Object.assign({}, DEFAULT_CFG);
      save();
      render();
    });
  }

  // Keep popup in sync if the content script writes (e.g. learned colors, picked selectors).
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes[STORE_KEY]) return;
    cfg = Object.assign({}, DEFAULT_CFG, changes[STORE_KEY].newValue);
    render();
  });

  document.addEventListener("DOMContentLoaded", init);
})();
