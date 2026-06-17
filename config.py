"""Configuration loaded from environment / .env file.

All tunable settings live here so the rest of the code never reads os.environ
directly. Copy .env.example to .env and fill in your values.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value if value is not None else ""


def _int(name: str, default: str = "0") -> int:
    raw = _get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _float(name: str, default: str) -> float:
    raw = _get(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# --- Discord ---------------------------------------------------------------
DISCORD_TOKEN = _get("DISCORD_TOKEN", required=True)
GUILD_ID = _int("GUILD_ID")
OWNER_ID = _int("OWNER_ID")
STAFF_ROLE_ID = _int("STAFF_ROLE_ID")
TICKET_CATEGORY_ID = _int("TICKET_CATEGORY_ID")

# --- Product ---------------------------------------------------------------
PRODUCT_NAME = _get("PRODUCT_NAME", "Product")
PRODUCT_PRICE_USD = _float("PRODUCT_PRICE_USD", "10")

# A small permanent "test" product so you can run a cheap end-to-end test
# without changing your real product. Off by default — set
# TEST_PRODUCT_ENABLED=true to show it.
TEST_PRODUCT_ENABLED = _get("TEST_PRODUCT_ENABLED", "false").lower() in (
    "1", "true", "yes", "on"
)
TEST_PRODUCT_NAME = _get("TEST_PRODUCT_NAME", "test")
TEST_PRODUCT_PRICE_USD = _float("TEST_PRODUCT_PRICE_USD", "0.5")

# The list of products shown on the panel. Each is {id, name, price_usd}.
PRODUCTS: list[dict] = [
    {"id": "main", "name": PRODUCT_NAME, "price_usd": PRODUCT_PRICE_USD},
]
if TEST_PRODUCT_ENABLED:
    PRODUCTS.append(
        {"id": "test", "name": TEST_PRODUCT_NAME, "price_usd": TEST_PRODUCT_PRICE_USD}
    )

ORDER_EXPIRY_MINUTES = _int("ORDER_EXPIRY_MINUTES", "30")
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", "45")

# --- Support tickets -------------------------------------------------------
# Role pinged when a support ticket opens. Defaults to STAFF_ROLE_ID if set.
SUPPORT_ROLE_ID = _int("SUPPORT_ROLE_ID") or STAFF_ROLE_ID
# Reasons shown in the support panel buttons (comma-separated to override).
SUPPORT_REASONS = [
    r.strip()
    for r in _get(
        "SUPPORT_REASONS", "Payment issue,Question,Feedback,Other"
    ).split(",")
    if r.strip()
]

# --- Transcripts -----------------------------------------------------------
# When a ticket is closed, its transcript is posted to a channel. Purchase/order
# tickets and support tickets go to separate channels. Set the *_ID to target a
# specific channel, otherwise the bot finds one by *_NAME.
PURCHASE_TRANSCRIPT_CHANNEL_ID = _int("PURCHASE_TRANSCRIPT_CHANNEL_ID")
PURCHASE_TRANSCRIPT_CHANNEL_NAME = _get(
    "PURCHASE_TRANSCRIPT_CHANNEL_NAME", "purchase-old-tix"
)
SUPPORT_TRANSCRIPT_CHANNEL_ID = _int("SUPPORT_TRANSCRIPT_CHANNEL_ID")
SUPPORT_TRANSCRIPT_CHANNEL_NAME = _get(
    "SUPPORT_TRANSCRIPT_CHANNEL_NAME", "support-old-tix"
)

# --- Invite tracking -------------------------------------------------------
# Channel where join/leave + "invited by" messages are posted. Needs the
# Server Members Intent and the bot to have "Manage Server" permission.
INVITE_LOG_CHANNEL_ID = _int("INVITE_LOG_CHANNEL_ID")
INVITE_LOG_CHANNEL_NAME = _get("INVITE_LOG_CHANNEL_NAME", "invite-log")

# Where the SQLite order log lives. On a host with ephemeral disk (e.g. Railway)
# point this at a mounted volume path so orders survive redeploys.
DATABASE_PATH = _get("DATABASE_PATH", "orders.db")

# --- APIs ------------------------------------------------------------------
ETHERSCAN_API_KEY = _get("ETHERSCAN_API_KEY", "")
ETHERSCAN_API_URL = _get("ETHERSCAN_API_URL", "https://api.etherscan.io/v2/api")
ETHERSCAN_CHAIN_ID = _get("ETHERSCAN_CHAIN_ID", "1")
BLOCKCYPHER_TOKEN = _get("BLOCKCYPHER_TOKEN", "")
SOLANA_RPC_URL = _get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
COINGECKO_API_URL = _get("COINGECKO_API_URL", "https://api.coingecko.com/api/v3")
TRONGRID_API_URL = _get("TRONGRID_API_URL", "https://api.trongrid.io")
TRONGRID_API_KEY = _get("TRONGRID_API_KEY", "")

# Token contract / mint addresses (overridable, but these are the mainnet ones).
USDT_TRON_CONTRACT = _get("USDT_TRON_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
USDC_ETH_CONTRACT = _get("USDC_ETH_CONTRACT", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
USDC_SOL_MINT = _get("USDC_SOL_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
USDT_SOL_MINT = _get("USDT_SOL_MINT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB")


# --- Coin metadata ---------------------------------------------------------
# `kind`       : which blockchain fetcher handles it (ltc/sol/eth/trc20/erc20/spl).
# `ticker`     : short symbol shown to buyers (LTC, USDT, USDC, ...).
# `decimals`   : how many decimal places we track/display for matching.
# `offset_step`: granularity of the unique per-order amount nudge (in coin units).
#                Each order's amount is bumped by a small random multiple of this
#                so two simultaneous buyers never share the same expected amount.
#                Kept tiny (well under 1 cent) so it doesn't affect the price.
COINS: dict[str, dict] = {
    "LTC": {
        "name": "Litecoin",
        "ticker": "LTC",
        "kind": "ltc",
        "coingecko_id": "litecoin",
        "decimals": 8,
        "offset_step": 0.00001,
        "address": _get("LTC_ADDRESS", ""),
        "min_conf": _int("LTC_MIN_CONFIRMATIONS", "1"),
        "color": 0x345D9D,
        "uri_scheme": "litecoin",
    },
    "SOL": {
        "name": "Solana",
        "ticker": "SOL",
        "kind": "sol",
        "coingecko_id": "solana",
        "decimals": 9,
        "offset_step": 0.00001,
        "address": _get("SOL_ADDRESS", ""),
        "min_conf": _int("SOL_MIN_CONFIRMATIONS", "1"),
        "color": 0x14F195,
        "uri_scheme": "solana",
    },
    "ETH": {
        "name": "Ethereum",
        "ticker": "ETH",
        "kind": "eth",
        "coingecko_id": "ethereum",
        "decimals": 8,
        "offset_step": 0.000001,
        "address": _get("ETH_ADDRESS", ""),
        "min_conf": _int("ETH_MIN_CONFIRMATIONS", "2"),
        "color": 0x627EEA,
        "uri_scheme": "ethereum",
    },
    "USDT_TRON": {
        "name": "USDT (Tron)",
        "ticker": "USDT",
        "kind": "trc20",
        "coingecko_id": "tether",
        "decimals": 6,
        "offset_step": 0.001,
        "address": _get("USDT_TRON_ADDRESS", ""),
        "contract": USDT_TRON_CONTRACT,
        "min_conf": _int("USDT_TRON_MIN_CONFIRMATIONS", "1"),
        "color": 0x26A17B,
        "uri_scheme": None,
    },
    "USDT_SOL": {
        "name": "USDT (Solana)",
        "ticker": "USDT",
        "kind": "spl",
        "coingecko_id": "tether",
        "decimals": 6,
        "offset_step": 0.001,
        "address": _get("USDT_SOL_ADDRESS", ""),
        "mint": USDT_SOL_MINT,
        "min_conf": _int("USDT_SOL_MIN_CONFIRMATIONS", "1"),
        "color": 0x26A17B,
        "uri_scheme": None,
    },
    "USDC_SOL": {
        "name": "USDC (Solana)",
        "ticker": "USDC",
        "kind": "spl",
        "coingecko_id": "usd-coin",
        "decimals": 6,
        "offset_step": 0.001,
        "address": _get("USDC_SOL_ADDRESS", ""),
        "mint": USDC_SOL_MINT,
        "min_conf": _int("USDC_SOL_MIN_CONFIRMATIONS", "1"),
        "color": 0x2775CA,
        "uri_scheme": None,
    },
    "USDC_ETH": {
        "name": "USDC (Ethereum)",
        "ticker": "USDC",
        "kind": "erc20",
        "coingecko_id": "usd-coin",
        "decimals": 6,
        "offset_step": 0.001,
        "address": _get("USDC_ETH_ADDRESS", ""),
        "contract": USDC_ETH_CONTRACT,
        "min_conf": _int("USDC_ETH_MIN_CONFIRMATIONS", "2"),
        "color": 0x2775CA,
        "uri_scheme": None,
    },
}

# Native coins shown as direct buttons on the panel (one each).
DIRECT_COINS = ["LTC", "SOL", "ETH"]

# Stablecoins are offered via a button -> network sub-menu. Ethereum networks
# are intentionally left out (they'd need an Etherscan API key).
STABLE_GROUPS = {
    "USDT": ["USDT_TRON", "USDT_SOL"],
    "USDC": ["USDC_SOL"],
}

# Friendly network name shown on each sub-menu button.
NETWORK_NAMES = {
    "USDT_TRON": "Tron",
    "USDT_SOL": "Solana",
    "USDC_SOL": "Solana",
    "USDC_ETH": "Ethereum",
}


def enabled_coins() -> dict[str, dict]:
    """Coins that have a receiving address configured."""
    return {code: meta for code, meta in COINS.items() if meta["address"]}


def enabled_group_networks(ticker: str) -> dict[str, dict]:
    """Configured (address-set) networks for a stablecoin group, e.g. 'USDT'."""
    return {c: COINS[c] for c in STABLE_GROUPS.get(ticker, []) if COINS[c]["address"]}
