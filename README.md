# Crypto Payment Ticket Bot

A Discord bot for selling a product with **crypto payments that are detected
automatically**. Buyers pick a coin, a private ticket opens, they pay the exact
amount shown, and the moment the payment confirms on-chain the bot **pings you**
so you can hand over the key.

- 💳 Accepts **Litecoin (LTC)**, **Solana (SOL)**, and **Ethereum (ETH)**
- 🎟️ Button-based **ticket system** — one private channel per buyer
- 💵 Live USD→crypto pricing (default **$10**), via CoinGecko
- 🔎 Watches the blockchain and **auto-detects** the payment
- 🔔 Pings the owner in the ticket on confirmation for **manual key delivery**
- 🧾 QR codes, order expiry, and a SQLite order log

---

## How it works

1. You run `/panel` once — it posts a purchase panel with three buttons.
2. A buyer clicks **Pay with Litecoin / Solana / Ethereum**.
3. The bot opens a **private ticket channel** (buyer + staff only), looks up the
   live price, and shows the **exact amount** to send + your address + a QR code.
4. A background task scans the chain every `POLL_INTERVAL_SECONDS`. When it sees
   the matching payment with enough confirmations, it **pings you** in the ticket.
5. You DM/post the product key. Click **Close ticket** when done.

### Why a "unique amount" per order?

You deliver keys by hand, so the bot uses **one address per coin** and makes
each order's amount slightly unique (e.g. `0.1473281` vs `0.1426210` LTC). That
tiny difference (well under a cent) lets the bot tell two simultaneous buyers
apart on a shared address — no hot wallet or xpub/seed needed. Buyers **must
send the exact amount shown.**

> This is a self-serve detector, not an escrow/custody service. The bot never
> holds funds — payments go straight to your wallet addresses.

---

## Setup

### 1. Create the bot application
1. Go to <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Add Bot** → copy the **token**.
3. Under **Privileged Gateway Intents**, none are required (defaults are fine).
4. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, bot
   permissions `Manage Channels`, `Send Messages`, `Embed Links`,
   `Attach Files`, `Read Message History`. Invite the bot to your server.

### 2. Configure
```bash
cp .env.example .env
# then edit .env and fill in:
#   DISCORD_TOKEN, GUILD_ID, OWNER_ID
#   LTC_ADDRESS / SOL_ADDRESS / ETH_ADDRESS   (your receiving wallets)
#   ETHERSCAN_API_KEY                         (free, for ETH detection)
```
Enable **Developer Mode** in Discord (Settings → Advanced) to copy IDs by
right-clicking your server / yourself / a category.

Leave any coin's address blank to disable that button.

### 3. Install & run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

### 4. Post the panel
In your server, run **`/panel`** (owner only). Done — buyers can now purchase.

---

## Deploy to Railway (always-on, recommended)

A Discord bot only works while it's running, so for a real shop host it
somewhere that stays online. Railway is the easiest:

1. Push this repo to GitHub.
2. Go to <https://railway.app> → **New Project → Deploy from GitHub repo** →
   pick this repo. Railway auto-detects Python and uses the included
   `Procfile` (`worker: python bot.py`).
3. Open the service → **Variables** tab and add the same keys from
   `.env.example` (`DISCORD_TOKEN`, `GUILD_ID`, `OWNER_ID`, your wallet
   addresses, `ETHERSCAN_API_KEY`, etc.). You do **not** upload a `.env` file —
   Railway injects these as environment variables.
4. **Persist orders:** add a **Volume** (e.g. mounted at `/data`) and set
   `DATABASE_PATH=/data/orders.db`. Without this, the order log resets on every
   redeploy.
5. Deploy. Watch the **Logs** tab for `Logged in as …`, then run `/panel` in
   your server.

The same steps work on any host (Render, Fly.io, a VPS) — it's just a worker
process that runs `python bot.py` with the env vars set.

---

## API keys / endpoints

| Coin | Source (default)        | Needs a key? |
|------|-------------------------|--------------|
| ETH  | Etherscan API v2        | **Yes** — free at <https://etherscan.io/apis> |
| LTC  | Blockcypher             | Optional token raises rate limits |
| SOL  | Solana JSON-RPC         | Public RPC works for testing; use [Helius](https://helius.dev)/QuickNode for production |
| Price| CoinGecko free API      | No |

The public Solana RPC is rate-limited and may block address lookups — for real
traffic, paste a Helius/QuickNode URL into `SOLANA_RPC_URL`.

---

## Configuration reference

All settings live in `.env` (see `.env.example` for the full list). Highlights:

| Variable | What it does |
|----------|--------------|
| `PRODUCT_NAME` / `PRODUCT_PRICE_USD` | Product label and price (default `$10`) |
| `OWNER_ID` | Who gets pinged on payment (you) |
| `STAFF_ROLE_ID` | Role that can see every ticket (optional) |
| `TICKET_CATEGORY_ID` | Category to nest tickets under (optional) |
| `ORDER_EXPIRY_MINUTES` | Unpaid orders auto-expire after this |
| `POLL_INTERVAL_SECONDS` | How often the chain is scanned |
| `*_MIN_CONFIRMATIONS` | Confirmations required before "paid" |

---

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Discord client, buttons, tickets, the watcher loop, `/panel` |
| `config.py` | Loads `.env`, coin metadata |
| `database.py` | SQLite order store |
| `prices.py` | CoinGecko USD→crypto conversion |
| `chains.py` | Per-coin blockchain scanners (LTC/ETH/SOL) |
| `payments.py` | Unique-amount generation + payment matching |

---

## Notes & limits

- **Send the exact amount.** Detection matches on the unique amount; a wrong
  amount won't auto-confirm (you can still verify and deliver manually).
- The bot **does not custody funds** and does not auto-refund.
- For high volume, swap the public APIs for keyed providers and consider
  per-order addresses derived from an xpub.
- Always test with a small real payment before going live.
