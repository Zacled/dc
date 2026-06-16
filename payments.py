"""Order-amount generation and the payment-matching scan.

This module is the brain that ties chains.py (what's on-chain) to database.py
(what we're waiting for). It stays Discord-agnostic: bot.py passes in async
callbacks for what to do when a payment is detected / confirmed / expired.
"""

import logging
import math
import random
import time
from collections.abc import Awaitable, Callable

import aiohttp

import config
import database as db
from chains import fetch_incoming

log = logging.getLogger("payments")

# Async callbacks: (order_row, payment_dict_or_None) -> None
Callback = Callable[..., Awaitable[None]]


def _quantize_up(amount: float, decimals: int) -> float:
    """Round an amount UP to `decimals` places (never undercharge)."""
    factor = 10**decimals
    return math.ceil(amount * factor) / factor


def make_unique_amount(coin: str, base_amount: float) -> float:
    """Nudge the base amount by a tiny unique offset.

    Two buyers paying the same coin at the same time would otherwise owe the
    exact same amount, and we couldn't tell their payments apart on a shared
    address. We add a small random multiple of the coin's `offset_step` and
    make sure it isn't already in use by another open order.
    """
    meta = config.COINS[coin]
    decimals = meta["decimals"]
    step = meta["offset_step"]
    in_use = db.get_pending_amounts(coin)

    base = _quantize_up(base_amount, decimals)
    for _ in range(200):
        offset = random.randint(1, 500) * step
        candidate = round(base + offset, decimals)
        if candidate not in in_use:
            return candidate
    # Extremely unlikely fallback: just return the base.
    return base


def _matches(payment: dict, order, tolerance: float) -> bool:
    """Does this on-chain payment correspond to this order's expected amount?"""
    return abs(payment["amount"] - order["expected_amount"]) <= tolerance


async def scan_once(
    session: aiohttp.ClientSession,
    *,
    on_detected: Callback,
    on_confirmed: Callback,
    on_expired: Callback,
) -> None:
    """Run a single pass over all active orders and update their state."""
    active = db.get_active_orders()
    if not active:
        return

    used_txids = db.get_used_txids()
    now = int(time.time())
    expiry_seconds = config.ORDER_EXPIRY_MINUTES * 60

    # Group by coin so we hit each chain's API once per scan.
    by_coin: dict[str, list] = {}
    for order in active:
        by_coin.setdefault(order["coin"], []).append(order)

    for coin, orders in by_coin.items():
        meta = config.COINS.get(coin)
        if not meta or not meta["address"]:
            continue

        try:
            incoming = await fetch_incoming(coin, session, meta["address"])
        except Exception as exc:  # noqa: BLE001 - one bad API call shouldn't kill the loop
            log.warning("Failed to scan %s: %s", coin, exc)
            incoming = None

        tolerance = meta["offset_step"] / 2 + 1e-12

        for order in orders:
            # Expire stale, still-unpaid orders (don't expire ones we've seen).
            if (
                order["status"] == "pending"
                and now - order["created_at"] > expiry_seconds
            ):
                db.set_status(order["id"], "expired")
                await on_expired(order, None)
                continue

            if not incoming:
                continue

            for payment in incoming:
                # Skip txids already credited to a *different* order. A payment
                # may still match the order that already owns it (e.g. upgrading
                # from "detected" to "confirmed" on a later scan).
                if payment["txid"] in used_txids and payment["txid"] != order["txid"]:
                    continue
                if not _matches(payment, order, tolerance):
                    continue

                if payment["confirmations"] < meta["min_conf"]:
                    # Seen but not yet final. Tell the buyer once.
                    if order["status"] != "detected":
                        db.mark_detected(order["id"], payment["txid"])
                        await on_detected(order, payment)
                else:
                    db.mark_paid(order["id"], payment["txid"])
                    used_txids.add(payment["txid"])
                    await on_confirmed(order, payment)
                break
