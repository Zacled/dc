# Dice Color Control

A small browser extension (Chrome / Edge / Brave, Manifest V3) that lets you
choose which **color** is shown on [onlinedice.org](https://onlinedice.org/).

> **What it actually does:** it overrides what *your own browser* paints for the
> result, in your tab only. It does not contact the site's servers, change any
> server-side value, or affect anyone else's screen. Think of it as a local
> userscript for demos, screenshots, mockups, teaching, or pranks. Don't use it
> to misrepresent a "fair" roll to other people.

Because it works by letting you **click the result element on the page**, it
doesn't depend on the site's internal HTML — it adapts to whatever element the
color dice uses.

## Install (load unpacked)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select the `dice-color-control/` folder.
4. Pin the extension if you like (puzzle-piece icon → pin).

> Firefox: it uses MV3 APIs that mostly map over; load it via
> `about:debugging` → "This Firefox" → "Load Temporary Add-on" and pick
> `manifest.json`. If a call errors, swap `chrome.*` for `browser.*`.

## Use

1. Go to **https://onlinedice.org/** and open the **color** dice/tool.
2. Click the extension icon to open the popup.
3. Click **🎯 Pick color box**, then click the colored die / box on the page.
   (Press **Esc** to cancel.) The popup will show a saved CSS selector.
4. *(Optional)* Click **🏷️ Pick name text** and click the element that shows
   the color's name, if the site displays one — the extension can overwrite it
   too. Keep **"Also overwrite color name text"** checked for this.
5. Pick a **Target color** — a preset swatch, or set a custom color + name.
6. Flip the **Force** switch (top-right) **on**.
7. Roll. The result will be your chosen color every time.

### Modes

- **Fixed** — every roll shows the one target color.
- **Sequence** — define an ordered list (use **+ Add current to sequence**),
  and each roll shows the next color in the list, looping.

### Match the site's real colors

Turn on **Learn site colors** and roll a few times with Force *off*. The
extension records the exact colors (and names, if you picked a name element)
the site naturally shows. They appear as swatches you can click to use as your
target — so a forced result is indistinguishable from a genuine one.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest, scoped to `onlinedice.org`. |
| `content.js` | Runs on the page: element picker, mutation observer, color forcing. |
| `popup.html` / `popup.css` / `popup.js` | The control panel UI. |

## Troubleshooting

- **Pick does nothing / "reload the dice page":** reload onlinedice.org after
  installing, then try again (content scripts only inject on fresh loads).
- **Color reverts for a frame:** the site repaints first, then we override; a
  brief flash is normal.
- **Wrong element captured:** click **Pick color box** again and select the
  actual colored area (zoom in if it's small).
