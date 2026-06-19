# Online-Dice Ad Blocker

A small browser extension that blocks ads on **online-dice.com** and gets past
its "turn off your ad blocker" detector.

## Why it's built this way

online-dice.com doesn't just hide if ads are missing — it actively *loads*
Google's ad script and shows its "AD BLOCK DETECTED" wall if that request
**fails**. So a naive blocker that drops the request actually *triggers* the
wall. The trick is to make the ad script load successfully but do nothing.

It works in three layers:

1. **Surrogate redirect** (`rules.json` → `adsbygoogle-stub.js` / `gpt-stub.js`)
   — instead of blocking Google's `adsbygoogle.js` / `gpt.js`, the extension
   *redirects* them to harmless local fakes. The request returns HTTP 200, so
   the detector thinks ads loaded fine, but the fakes never render an ad.
2. **Detector bypass** (`antiadblock.js`) — runs before the site's scripts and
   seeds convincing fakes (`adsbygoogle`, `google_ad_status = 1`, `canRunAds`,
   FuckAdBlock/BlockAdBlock) in case the page checks those directly.
3. **Wall remover** (`cleanup.js`) — backup: if the "AD BLOCK DETECTED" screen
   still shows, it finds it by its wording, removes it, and re-enables
   scrolling. Unrelated ad networks (Taboola, Criteo, etc.) are still hard
   blocked.

## Install (Chrome / Edge / Brave / Opera)

1. Download/clone this folder (`online-dice-adblocker`) somewhere on your
   computer.
2. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked** and select the `online-dice-adblocker` folder.
5. Reload https://www.online-dice.com/ — the ads and the nag screen should be
   gone.

That's it. The extension only runs its detector-bypass code on
`online-dice.com`; the network ad-blocking rules apply everywhere, which just
means fewer ads on other sites too.

## Firefox

Firefox supports Manifest V3, but you load it differently and `world: "MAIN"`
needs Firefox 128+. Go to `about:debugging` → **This Firefox** → **Load
Temporary Add-on** and pick the `manifest.json`. (Temporary add-ons unload when
you close Firefox; for permanent use it would need signing.)

## After installing: do a hard reload

Reload the page with the cache cleared so the old ad script isn't reused:
**Ctrl+Shift+R** (Windows) / **Cmd+Shift+R** (Mac). If you already had another
ad blocker (uBlock, AdBlock) running on this site, turn it off here — two
blockers can fight and one of them may still trip the wall.

## If the nag screen still appears

Two things to try:

1. The site may have changed its wording. Open `cleanup.js`, find the
   `KEYWORDS` / `STRONG` lists near the top, add a distinctive phrase from the
   wall (lower-cased), save, then hit the reload icon on the extension card.

2. Help me tailor it. Open DevTools (**F12**) → **Network** tab → reload the
   page. Look for a request to `pagead2.googlesyndication.com` or
   `securepubads.g.doubleclick.net`. Click it and check the **Status**:
   - If it shows as redirected to the extension (or `200`), the bypass is
     working and the wall is using a different signal — grab the script that
     prints "AD BLOCK DETECTED" (search the **Sources** tab for `adblock` or
     `detected`) and send it to me; I'll write an exact counter.
   - If it shows blocked/failed (red), the redirect rule didn't apply — make
     sure no other ad blocker is also active on the site.

### Guaranteed fallback

If you just want it gone with zero fiddling, install **uBlock Origin** (Chrome
Web Store / Firefox Add-ons) and enable its *"Annoyances — anti-adblock"*
filter list (Settings → Filter lists). uBlock's maintainers keep dedicated,
constantly-updated surrogates for exactly these AdSense walls and will out-run
a hand-rolled extension over time. This custom extension is the tailored,
learn-how-it-works option; uBlock is the bulletproof one.

## Files

| File                 | Purpose                                                       |
|----------------------|---------------------------------------------------------------|
| `manifest.json`      | Extension definition (MV3).                                   |
| `rules.json`         | Redirect rules for Google loaders + blocks for other networks.|
| `adsbygoogle-stub.js`| Fake AdSense loader (loads OK, shows nothing).                |
| `gpt-stub.js`        | Fake Google Publisher Tag loader.                             |
| `antiadblock.js`     | Pre-seeds fake ad globals so detection passes (MAIN world).   |
| `cleanup.js`         | Removes the "AD BLOCK DETECTED" wall and undoes scroll-lock.   |

## Note

This only changes what runs in **your own browser**. It blocks ads and the
detector wall the same way mainstream blockers (uBlock Origin, etc.) do. Ad
revenue is how many free sites stay up — if you find online-dice useful,
consider supporting it some other way.
