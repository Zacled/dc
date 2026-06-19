/*
 * antiadblock.js  —  runs in the page's MAIN world at document_start,
 * i.e. BEFORE the site's own scripts execute.
 *
 * Ad-block detectors usually work by one of these tricks:
 *   1. Loading AdSense (adsbygoogle) and checking whether it filled.
 *   2. Loading a tiny script (ads.js / advert.js) that sets a global like
 *      `window.canRunAds = true`. If the script was blocked, the global is
 *      missing, so the site assumes a blocker is present.
 *   3. Using the BlockAdBlock / FuckAdBlock library and reacting to its
 *      "detected" callback.
 *
 * We pre-seed believable fakes for all three so the detector concludes that
 * ads are running normally, even though the real ad requests are blocked by
 * the declarativeNetRequest rules.
 */
(function () {
  'use strict';

  // Define a property that the page can read but cannot overwrite, so the
  // real ad script can't replace our fake once it (fails to) load.
  function lock(name, value) {
    try {
      Object.defineProperty(window, name, {
        configurable: false,
        enumerable: true,
        get: function () { return value; },
        set: function () { /* swallow the site's attempt to overwrite */ }
      });
    } catch (e) {
      try { window[name] = value; } catch (e2) {}
    }
  }

  // ---- 1. Google AdSense stub -------------------------------------------
  // Pages do: (adsbygoogle = window.adsbygoogle || []).push({})
  // We hand them an array whose push() is a harmless no-op.
  try {
    var ads = window.adsbygoogle && window.adsbygoogle.length ? window.adsbygoogle : [];
    ads.loaded = true;
    ads.push = function () { return ads.length; };
    lock('adsbygoogle', ads);
  } catch (e) {}

  // ---- 2. "ads are allowed" flags that bait scripts normally set --------
  lock('canRunAds', true);
  lock('canShowAds', true);
  lock('canADS', true);
  lock('isAdsDisplayed', true);
  // Some detectors flip a "blocker present" flag instead — keep it false.
  lock('adBlockDetected', false);
  lock('adblockDetected', false);
  lock('isAdBlockActive', false);

  // ---- 3. BlockAdBlock / FuckAdBlock neutralisation ---------------------
  // The real library exposes instances (fuckAdBlock / blockAdBlock) and the
  // classes (FuckAdBlock / BlockAdBlock). We replace them with a stub that
  // only ever fires the "not detected" path.
  function Stub() {}
  Stub.prototype.setOption    = function () { return this; };
  Stub.prototype.check        = function () { return false; };          // "no blocker"
  Stub.prototype.onDetected   = function () { return this; };           // never call
  Stub.prototype.onNotDetected= function (cb) { if (typeof cb === 'function') setTimeout(cb, 0); return this; };
  Stub.prototype.on           = function (detected, cb) {
    if (!detected && typeof cb === 'function') setTimeout(cb, 0);
    return this;
  };
  Stub.prototype.emitEvent    = function () { return this; };
  Stub.prototype.clearEvent   = function () { return this; };

  var instance = new Stub();
  lock('FuckAdBlock', Stub);
  lock('BlockAdBlock', Stub);
  lock('fuckAdBlock', instance);
  lock('blockAdBlock', instance);
  lock('sniffAdBlock', instance);
  lock('SniffAdBlock', Stub);

  // ---- 4. Neutralise common timer-based "show the wall later" calls -----
  // Some detectors run their check on a delay via a named global function.
  // If the site defines one of these we leave it; we only stop ours.
})();
