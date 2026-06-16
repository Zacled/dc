"""Live USD price lookups via the free CoinGecko API."""

import logging

import aiohttp

import config

log = logging.getLogger("prices")


class PriceError(Exception):
    """Raised when a price could not be fetched."""


async def get_usd_price(session: aiohttp.ClientSession, coingecko_id: str) -> float:
    """Return the current USD price for a single CoinGecko coin id."""
    url = f"{config.COINGECKO_API_URL}/simple/price"
    params = {"ids": coingecko_id, "vs_currencies": "usd"}
    try:
        async with session.get(url, params=params, timeout=20) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the caller
        raise PriceError(f"Could not reach the price feed: {exc}") from exc

    price = data.get(coingecko_id, {}).get("usd")
    if not price:
        raise PriceError(f"No USD price returned for {coingecko_id}")
    return float(price)


async def usd_to_coin(
    session: aiohttp.ClientSession, coingecko_id: str, usd_amount: float
) -> tuple[float, float]:
    """Convert a USD amount to a coin amount.

    Returns (coin_amount, coin_usd_price).
    """
    price = await get_usd_price(session, coingecko_id)
    return usd_amount / price, price
