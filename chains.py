"""Blockchain payment scanners.

Each fetcher returns a list of *incoming* payments to a watched address, as
dicts shaped like:

    {"txid": str, "amount": float, "confirmations": int, "timestamp": int | None}

`amount` is in whole coin units (LTC / SOL / ETH). We deliberately keep the
fetchers dumb: they just report what's been received. Matching an incoming
payment to a specific order happens in payments.py.

These use public/free APIs. For real volume, plug in your own API keys / RPC
endpoints via the .env file.
"""

import logging
from datetime import datetime, timezone

import aiohttp

import config

log = logging.getLogger("chains")


def _parse_iso(value: str | None) -> int | None:
    if not value:
        return None
    try:
        # Blockcypher timestamps look like "2023-01-02T03:04:05.123Z"
        cleaned = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(cleaned).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


# --- Litecoin (Blockcypher) ------------------------------------------------
async def fetch_ltc_incoming(
    session: aiohttp.ClientSession, address: str
) -> list[dict]:
    url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}"
    params: dict[str, str | int] = {"limit": 50}
    if config.BLOCKCYPHER_TOKEN:
        params["token"] = config.BLOCKCYPHER_TOKEN

    async with session.get(url, params=params, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json()

    payments: list[dict] = []
    refs = (data.get("txrefs") or []) + (data.get("unconfirmed_txrefs") or [])
    for ref in refs:
        # tx_input_n == -1 means this address received funds in that tx.
        if ref.get("tx_input_n", 0) != -1:
            continue
        payments.append(
            {
                "txid": ref["tx_hash"],
                "amount": ref.get("value", 0) / 1e8,
                "confirmations": ref.get("confirmations", 0),
                "timestamp": _parse_iso(ref.get("confirmed")),
            }
        )
    return payments


# --- Ethereum (Etherscan v2) ----------------------------------------------
async def fetch_eth_incoming(
    session: aiohttp.ClientSession, address: str
) -> list[dict]:
    params = {
        "chainid": config.ETHERSCAN_CHAIN_ID,
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "apikey": config.ETHERSCAN_API_KEY,
    }
    async with session.get(config.ETHERSCAN_API_URL, params=params, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json()

    payments: list[dict] = []
    # status "1" = ok; "0" with "No transactions found" is normal for a fresh addr.
    if str(data.get("status")) != "1":
        message = data.get("message", "")
        if "No transactions" not in message:
            log.warning("Etherscan returned: %s / %s", message, data.get("result"))
        return payments

    addr_lower = address.lower()
    for tx in data.get("result", []):
        if tx.get("to", "").lower() != addr_lower:
            continue  # outgoing or contract-internal; ignore
        if tx.get("isError") == "1":
            continue
        try:
            amount = int(tx["value"]) / 1e18
        except (KeyError, ValueError):
            continue
        payments.append(
            {
                "txid": tx["hash"],
                "amount": amount,
                "confirmations": int(tx.get("confirmations", 0)),
                "timestamp": int(tx["timeStamp"]) if tx.get("timeStamp") else None,
            }
        )
    return payments


# --- Solana (JSON-RPC) -----------------------------------------------------
async def _solana_rpc(
    session: aiohttp.ClientSession, method: str, params: list
) -> dict | list | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(config.SOLANA_RPC_URL, json=payload, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if "error" in data:
        log.warning("Solana RPC error on %s: %s", method, data["error"])
        return None
    return data.get("result")


async def fetch_sol_incoming(
    session: aiohttp.ClientSession, address: str
) -> list[dict]:
    signatures = await _solana_rpc(
        session, "getSignaturesForAddress", [address, {"limit": 25}]
    )
    if not signatures:
        return []

    payments: list[dict] = []
    for sig_info in signatures:
        if sig_info.get("err"):
            continue
        signature = sig_info["signature"]
        tx = await _solana_rpc(
            session,
            "getTransaction",
            [signature, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}],
        )
        if not tx:
            continue

        meta = tx.get("meta") or {}
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])

        index = None
        for i, key in enumerate(account_keys):
            pubkey = key.get("pubkey") if isinstance(key, dict) else key
            if pubkey == address:
                index = i
                break
        if index is None:
            continue

        pre = meta.get("preBalances", [])
        post = meta.get("postBalances", [])
        if index >= len(pre) or index >= len(post):
            continue

        delta_lamports = post[index] - pre[index]
        if delta_lamports <= 0:
            continue  # this address sent or paid fees, didn't receive

        status = sig_info.get("confirmationStatus")
        confirmations = 1 if status == "finalized" else 0
        payments.append(
            {
                "txid": signature,
                "amount": delta_lamports / 1e9,
                "confirmations": confirmations,
                "timestamp": sig_info.get("blockTime"),
            }
        )
    return payments


_FETCHERS = {
    "LTC": fetch_ltc_incoming,
    "ETH": fetch_eth_incoming,
    "SOL": fetch_sol_incoming,
}


async def fetch_incoming(
    coin: str, session: aiohttp.ClientSession, address: str
) -> list[dict]:
    fetcher = _FETCHERS.get(coin)
    if fetcher is None:
        raise ValueError(f"No fetcher for coin {coin}")
    return await fetcher(session, address)
