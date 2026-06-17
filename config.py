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
# without changing your real product. Set TEST_PRODUCT_ENABLED=false to hide it.
TEST_PRODUCT_ENABLED = _get("TEST_PRODUCT_ENABLED", "true").lower() in (
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


# --- Coin metadata ---------------------------------------------------------
# `decimals`   : how many decimal places we track/display for matching.
# `offset_step`: granularity of the unique per-order amount nudge (in coin units).
#                Each order's amount is bumped by a small random multiple of this
#                so two simultaneous buyers never share the same expected amount.
#                Kept tiny (well under 1 cent) so it doesn't affect the price.
COINS: dict[str, dict] = {
    "LTC": {
        "name": "Litecoin",
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
        "coingecko_id": "ethereum",
        "decimals": 8,
        "offset_step": 0.000001,
        "address": _get("ETH_ADDRESS", ""),
        "min_conf": _int("ETH_MIN_CONFIRMATIONS", "2"),
        "color": 0x627EEA,
        "uri_scheme": "ethereum",
    },
}


def enabled_coins() -> dict[str, dict]:
    """Coins that have a receiving address configured."""
    return {code: meta for code, meta in COINS.items() if meta["address"]}
