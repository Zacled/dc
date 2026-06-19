/*
 * adsbygoogle-stub.js  —  surrogate served IN PLACE of Google's real
 * https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js
 *
 * The extension's declarativeNetRequest rules REDIRECT the real AdSense loader
 * to this file. Because the request returns HTTP 200 (this script) instead of
 * being blocked, the site's "did the ad script load?" detector is satisfied —
 * yet no real ad is ever fetched or rendered.
 */
(function () {
  'use strict';

  if (window.adsbygoogle && window.adsbygoogle.loaded === true) return;

  var slots = window.adsbygoogle || [];

  // Mark every <ins class="adsbygoogle"> as "filled" so size/state detectors
  // (data-adsbygoogle-status === 'done', offsetHeight > 0) are happy.
  function fill() {
    try {
      var list = document.querySelectorAll('ins.adsbygoogle');
      for (var i = 0; i < list.length; i++) {
        var ins = list[i];
        if (ins.getAttribute('data-adsbygoogle-status') === 'done') continue;
        ins.setAttribute('data-adsbygoogle-status', 'done');
        ins.setAttribute('data-ad-status', 'filled');
        if (!ins.style.minHeight) ins.style.minHeight = '1px';
        if (!ins.style.minWidth) ins.style.minWidth = '1px';
      }
    } catch (e) {}
  }

  slots.loaded = true;
  // push() is what pages call to request an ad — make it a harmless no-op.
  slots.push = function () { fill(); return 1; };

  try {
    Object.defineProperty(window, 'adsbygoogle', {
      configurable: false,
      get: function () { return slots; },
      set: function () {}
    });
  } catch (e) { window.adsbygoogle = slots; }

  // Signals real AdSense sets when an ad is served (1 = served). Detectors
  // frequently test these instead of, or in addition to, the script load.
  try { window.google_ad_status = 1; } catch (e) {}
  window.__google_ad_urls = window.__google_ad_urls || [];
  window.google_ad_modifications = window.google_ad_modifications || { eids: [] };

  // Drain anything already queued, then keep filling as the DOM grows.
  fill();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fill);
  }
  try {
    new MutationObserver(fill).observe(document.documentElement, {
      childList: true, subtree: true
    });
  } catch (e) {}
})();
