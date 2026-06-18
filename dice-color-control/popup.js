/* Dice Color Control — popup (block / force colors) */
(() => {
  "use strict";

  const STORE_KEY = "dcc";

  const COLORS = [
    { name: "Red", css: "#e23b3b" },
    { name: "Orange", css: "#ef8a2b" },
    { name: "Yellow", css: "#f4c531" },
    { name: "Green", css: "#34a23f" },
    { name: "Blue", css: "#3f6fd1" },
    { name: "Purple", css: "#8e44c9" }
  ];

  const DEFAULT_CFG = {
    enabled: true,
    blocked: [],
    forced: "",
    manualSelector: ""
  };

  let cfg = Object.assign({}, DEFAULT_CFG);
  let tabId = null;

  const $ = (id) => document.getElementById(id);
  const save = () => chrome.storage.local.set({ [STORE_KEY]: cfg });

  // ---- render ---------------------------------------------------------------

  function render() {
    $("enabled").checked = !!cfg.enabled;

    const block = $("blockRow");
    const force = $("forceRow");
    block.innerHTML = "";
    force.innerHTML = "";

    for (const c of COLORS) {
      // Block chip
      const b = document.createElement("div");
      b.className = "chip" + (cfg.blocked.includes(c.name) ? " blocked" : "");
      b.style.background = c.css;
      b.textContent = c.name;
      if (cfg.blocked.includes(c.name)) {
        const m = document.createElement("span");
        m.className = "mark";
        m.textContent = "🚫";
        b.appendChild(m);
      }
      b.title = cfg.blocked.includes(c.name) ? `${c.name} blocked — tap to allow` : `Block ${c.name}`;
      b.addEventListener("click", () => toggleBlock(c.name));
      block.appendChild(b);

      // Force chip
      const f = document.createElement("div");
      f.className = "chip" + (cfg.forced === c.name ? " forced" : "");
      f.style.background = c.css;
      f.textContent = c.name;
      if (cfg.forced === c.name) {
        const m = document.createElement("span");
        m.className = "mark";
        m.textContent = "✅";
        f.appendChild(m);
      }
      f.title = cfg.forced === c.name ? `Forcing ${c.name} — tap to stop` : `Force ${c.name} on every die`;
      f.addEventListener("click", () => toggleForce(c.name));
      force.appendChild(f);
    }
  }

  function toggleBlock(name) {
    if (cfg.blocked.includes(name)) {
      cfg.blocked = cfg.blocked.filter((n) => n !== name);
    } else {
      cfg.blocked.push(name);
      if (cfg.forced === name) cfg.forced = ""; // can't force a blocked color
    }
    commit();
  }

  function toggleForce(name) {
    cfg.forced = cfg.forced === name ? "" : name;
    if (cfg.forced) cfg.blocked = cfg.blocked.filter((n) => n !== name); // forcing un-blocks it
    commit();
  }

  function commit() {
    cfg.enabled = true; // any action turns it on so something actually happens
    $("enabled").checked = true;
    save();
    render();
    if (tabId != null) chrome.tabs.sendMessage(tabId, { type: "applyNow" }, () => void chrome.runtime.lastError);
  }

  // ---- site connection ------------------------------------------------------

  function checkSite() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      const status = $("status");
      const isSite = tab && /^https:\/\/(www\.)?onlinedice\.org\//.test(tab.url || "");
      if (!isSite) {
        status.textContent = "Open onlinedice.org to use this.";
        status.className = "status warn";
        return;
      }
      tabId = tab.id;
      chrome.tabs.sendMessage(tabId, { type: "ping" }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          status.textContent = "Reload the dice page, then reopen this.";
          status.className = "status warn";
        } else if (resp.dice > 0) {
          status.textContent = `Connected — ${resp.dice} dice detected.`;
          status.className = "status ok";
        } else {
          status.textContent = "Connected, but no dice spotted yet — roll once.";
          status.className = "status warn";
        }
      });
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
      if (tabId != null) chrome.tabs.sendMessage(tabId, { type: "applyNow" }, () => void chrome.runtime.lastError);
    });

    $("forceOff").addEventListener("click", () => {
      cfg.forced = "";
      commit();
    });

    $("reset").addEventListener("click", () => {
      cfg = Object.assign({}, DEFAULT_CFG);
      save();
      render();
    });

    $("pick").addEventListener("click", () => {
      if (tabId == null) return checkSite();
      chrome.tabs.sendMessage(tabId, { type: "pick" }, () => window.close());
    });
    $("autoDetect").addEventListener("click", () => {
      cfg.manualSelector = "";
      save();
    });
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes[STORE_KEY]) return;
    cfg = Object.assign({}, DEFAULT_CFG, changes[STORE_KEY].newValue);
    render();
  });

  document.addEventListener("DOMContentLoaded", init);
})();
