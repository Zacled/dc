/*
 * gpt-stub.js  —  surrogate served IN PLACE of Google Publisher Tag
 * https://securepubads.g.doubleclick.net/tag/js/gpt.js
 *
 * Same idea as the AdSense stub: it loads successfully (so detectors that
 * watch gpt.js are satisfied) but every method is an inert no-op, so no ad
 * ever displays.
 */
(function () {
  'use strict';

  if (window.googletag && window.googletag.apiReady) return;

  var noopfn = function () {};
  var noopthis = function () { return this; };

  function Slot() {}
  var slotProto = Slot.prototype;
  [
    'addService', 'setTargeting', 'clearTargeting', 'defineSizeMapping',
    'setCollapseEmptyDiv', 'setForceSafeFrame', 'set', 'updateTargetingFromMap'
  ].forEach(function (m) { slotProto[m] = noopthis; });
  slotProto.getSlotElementId = function () { return ''; };
  slotProto.getAdUnitPath = function () { return ''; };

  function PubAdsService() {}
  var pubProto = PubAdsService.prototype;
  [
    'enableSingleRequest', 'disableInitialLoad', 'collapseEmptyDivs',
    'refresh', 'setTargeting', 'clearTargeting', 'setCentering',
    'addEventListener', 'removeEventListener', 'setRequestNonPersonalizedAds',
    'setPrivacySettings', 'set', 'get', 'updateCorrelator', 'display'
  ].forEach(function (m) { pubProto[m] = noopthis; });
  var pubads = new PubAdsService();

  var googletag = window.googletag || {};
  googletag.apiReady = true;
  googletag.cmd = googletag.cmd || [];
  googletag.defineSlot = function () { return new Slot(); };
  googletag.defineOutOfPageSlot = function () { return new Slot(); };
  googletag.pubads = function () { return pubads; };
  googletag.enableServices = noopfn;
  googletag.display = noopfn;
  googletag.destroySlots = noopfn;
  googletag.sizeMapping = function () {
    return { addSize: noopthis, build: function () { return []; } };
  };

  // Run already-queued callbacks, then make future cmd.push() execute at once.
  var queue = googletag.cmd;
  googletag.cmd = { push: function (fn) { try { fn(); } catch (e) {} return 1; } };
  if (queue && queue.length) {
    for (var i = 0; i < queue.length; i++) {
      try { queue[i](); } catch (e) {}
    }
  }

  window.googletag = googletag;
})();
