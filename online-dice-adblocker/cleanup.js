/*
 * cleanup.js  —  runs in the isolated content-script world.
 *
 * Second line of defence: if a "Please disable your ad blocker" overlay still
 * manages to appear, this finds it by its wording and removes it, then undoes
 * the side effects such walls usually apply (locked scrolling, blurred page).
 *
 * It also keeps ad "bait" containers measurable so size-based detectors that
 * check `element.offsetHeight === 0` think the slot rendered.
 */
(function () {
  'use strict';

  // Phrases that an anti-adblock wall almost always contains. Lower-cased.
  // Add any wording you see in the site's popup here.
  var KEYWORDS = [
    'ad block', 'adblock', 'ad-block', 'ad blocker', 'adblocker',
    'disable your ad', 'disable adblock', 'turn off your ad',
    'whitelist', 'pause adblock', 'using an ad blocker',
    'we noticed you', 'support us by disabling',
    // exact phrases seen on online-dice.com's wall:
    'ad block detected', 'disable ad block', 'zero income',
    'support us', 'reduce ads', 'please disable'
  ];

  // The strongest, least-ambiguous markers of THIS site's wall. If an element's
  // own (short) text contains one of these, we treat it as the wall even if it
  // is laid out as normal page content rather than a floating overlay.
  var STRONG = ['ad block detected', 'disable ad block in your browser', 'zero income without ads'];

  var BAIT_SELECTOR =
    'ins.adsbygoogle, .adsbygoogle, .adsbox, .ad-box, #ads, .ads, .ad, ' +
    '[id^="ad-"], [class^="ad-"], [class*="advert"]';

  function textMatches(el) {
    var t = (el.textContent || '').toLowerCase();
    if (t.length > 4000) return false; // skip giant containers (e.g. <body>)
    for (var i = 0; i < KEYWORDS.length; i++) {
      if (t.indexOf(KEYWORDS[i]) !== -1) return true;
    }
    return false;
  }

  function looksLikeOverlay(el) {
    var s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'absolute') return false;
    var r = el.getBoundingClientRect();
    var coversScreen =
      r.width >= window.innerWidth * 0.6 && r.height >= window.innerHeight * 0.6;
    var z = parseInt(s.zIndex, 10) || 0;
    return coversScreen && z >= 100;
  }

  function strongMatch(el) {
    var t = (el.textContent || '').toLowerCase();
    if (t.length > 4000) return false;
    for (var i = 0; i < STRONG.length; i++) {
      if (t.indexOf(STRONG[i]) !== -1) return true;
    }
    return false;
  }

  function killWalls() {
    // 1a. Remove floating modal/overlay walls identified by wording.
    var nodes = document.querySelectorAll('div, section, aside, dialog');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!el || !el.parentNode) continue;
      if (looksLikeOverlay(el) && textMatches(el)) {
        el.parentNode.removeChild(el);
      }
    }

    // 1b. This site's wall renders as full-page content, not a popup. Find the
    // smallest element whose OWN text is the strong wall marker and walk up to
    // the widest ancestor that still only wraps the wall, then remove it. This
    // is a fallback; the redirect stubs should stop the wall from appearing.
    var hits = document.querySelectorAll('h1, h2, div, section');
    for (var k = 0; k < hits.length; k++) {
      var node = hits[k];
      if (!node || !node.parentNode) continue;
      // node's *direct* text must carry the marker (avoid matching huge wrappers)
      var own = (node.textContent || '').toLowerCase();
      if (own.length > 600) continue;
      if (!strongMatch(node)) continue;
      // climb to a big container, but never up to <body>/<html>
      var target = node;
      while (
        target.parentNode &&
        target.parentNode !== document.body &&
        target.parentNode !== document.documentElement &&
        (target.parentNode.textContent || '').length < 4000
      ) {
        target = target.parentNode;
      }
      if (target && target.parentNode) target.parentNode.removeChild(target);
    }

    // 2. Undo scroll-lock and blur that walls leave behind.
    [document.documentElement, document.body].forEach(function (el) {
      if (!el) return;
      el.style.setProperty('overflow', 'auto', 'important');
      el.style.setProperty('position', 'static', 'important');
      el.style.setProperty('filter', 'none', 'important');
      el.style.setProperty('-webkit-filter', 'none', 'important');
      el.style.setProperty('pointer-events', 'auto', 'important');
    });

    // 3. Keep ad bait slots "visible" so offsetHeight checks pass.
    var baits = document.querySelectorAll(BAIT_SELECTOR);
    for (var j = 0; j < baits.length; j++) {
      var b = baits[j];
      if (b.offsetHeight === 0 || b.offsetWidth === 0) {
        b.style.setProperty('display', 'block', 'important');
        b.style.setProperty('width', '1px', 'important');
        b.style.setProperty('height', '1px', 'important');
        b.style.setProperty('position', 'absolute', 'important');
        b.style.setProperty('left', '-9999px', 'important');
      }
    }
  }

  // Run as the DOM streams in, on every mutation, and on an early interval —
  // detectors frequently fire a second or two after load.
  var observer = new MutationObserver(killWalls);
  function start() {
    killWalls();
    observer.observe(document.documentElement || document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class']
    });
  }
  if (document.documentElement) start();
  else document.addEventListener('DOMContentLoaded', start);

  var ticks = 0;
  var iv = setInterval(function () {
    killWalls();
    if (++ticks > 20) clearInterval(iv); // ~10s of active polling, then stop
  }, 500);
})();
