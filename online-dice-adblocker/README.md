# Online-Dice Ad Blocker

A small browser extension that blocks ads on **online-dice.com** and gets past
its "turn off your ad blocker" detector.

It works in two layers:

1. **Network blocking** (`rules.json`) — uses the browser's built-in
   `declarativeNetRequest` engine to drop requests to AdSense, DoubleClick and
   ~20 other ad networks before they load.
2. **Detector bypass** (`antiadblock.js` + `cleanup.js`) — runs before the
   site's own scripts and feeds the page convincing fakes (`adsbygoogle`,
   `canRunAds`, the FuckAdBlock/BlockAdBlock library), so the detector believes
   ads loaded normally. If a nag overlay slips through anyway, `cleanup.js`
   removes it and re-enables scrolling.

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

## If the nag screen still appears

The site may use different wording than the defaults. Open `cleanup.js`, find
the `KEYWORDS` list near the top, and add a distinctive phrase from the popup
(lower-cased), e.g. `'please support our site'`. Save and reload the extension
on the extensions page.

To see exactly what the page is doing, open DevTools (F12) → **Network** tab,
reload, and look for blocked requests (shown in red) and any script that
mentions `adblock`.

## Files

| File             | Purpose                                                      |
|------------------|--------------------------------------------------------------|
| `manifest.json`  | Extension definition (MV3).                                  |
| `rules.json`     | List of ad-network domains to block.                         |
| `antiadblock.js` | Pre-seeds fake ad globals so detection passes (MAIN world).  |
| `cleanup.js`     | Removes any nag overlay and undoes scroll-lock / blur.       |

## Note

This only changes what runs in **your own browser**. It blocks ads and the
detector wall the same way mainstream blockers (uBlock Origin, etc.) do. Ad
revenue is how many free sites stay up — if you find online-dice useful,
consider supporting it some other way.
