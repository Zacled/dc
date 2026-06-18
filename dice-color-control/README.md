# Dice Color Control

A small Chrome / Edge / Brave extension (Manifest V3) for the **Color Dice** on
[onlinedice.org](https://onlinedice.org/). It does two things:

- **🚫 Block a color** — that color never shows up; the die is recolored to an
  allowed color instead.
- **✅ Force a color** — every die is shown in the color you pick.

The dice are detected automatically (no clicking/picking needed), and the site's
exact shades are matched so the result looks native.

> **What it really is:** a *local display override*. It only changes what your
> own browser paints in your tab. It does **not** change the site's actual
> result, the "Result ID / Verify" value, the roll history/stats, or anyone
> else's screen. Use it for demos, screenshots, jokes — not to mislead people
> about a "fair" or verifiable roll.

## Install (load unpacked)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select the `dice-color-control/` folder.

After installing, reload the onlinedice.org tab once.

## Use

1. Go to **onlinedice.org** and choose **Color Dice**.
2. Click the extension icon.
3. **Block:** tap any color in the *Block* row → it gets a 🚫 and won't appear.
4. **Force:** tap any color in the *Force* row → it gets a ✅ and every die shows
   it. Tap it again, or **Off (random)**, to stop.
5. Roll. The dice follow your choices.

The big **On** switch turns the whole thing off/on. **Reset** clears everything.

### If the colors don't change

Open **"Dice not changing?"** at the bottom, click **Pick the dice manually**,
then click one of the dice on the page. (Use **Back to auto** to undo.)

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest, scoped to `onlinedice.org`. |
| `content.js` | Detects the dice, classifies colors by hue, applies block/force. |
| `popup.html` / `popup.css` / `popup.js` | The block/force control panel. |
