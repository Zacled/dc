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


# --- USDC on Ethereum (ERC-20, via Etherscan tokentx) ----------------------
async def fetch_erc20_incoming(
    session: aiohttp.ClientSession, address: str, contract: str
) -> list[dict]:
    params = {
        "chainid": config.ETHERSCAN_CHAIN_ID,
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract,
        "address": address,
        "sort": "desc",
        "apikey": config.ETHERSCAN_API_KEY,
    }
    async with session.get(config.ETHERSCAN_API_URL, params=params, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json()

    payments: list[dict] = []
    if str(data.get("status")) != "1":
        return payments

    addr_lower = address.lower()
    for tx in data.get("result", []):
        if tx.get("to", "").lower() != addr_lower:
            continue
        try:
            decimals = int(tx.get("tokenDecimal", 6))
            amount = int(tx["value"]) / (10**decimals)
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


# --- USDT on Tron (TRC-20, via TronGrid) -----------------------------------
async def fetch_trc20_incoming(
    session: aiohttp.ClientSession, address: str, contract: str
) -> list[dict]:
    url = f"{config.TRONGRID_API_URL}/v1/accounts/{address}/transactions/trc20"
    params = {
        "only_to": "true",
        "only_confirmed": "true",
        "limit": 50,
        "contract_address": contract,
    }
    headers = {}
    if config.TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = config.TRONGRID_API_KEY

    async with session.get(url, params=params, headers=headers, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json()

    payments: list[dict] = []
    for tx in data.get("data", []):
        if tx.get("to") != address:
            continue
        try:
            decimals = tx.get("token_info", {}).get("decimals", 6)
            amount = int(tx["value"]) / (10**decimals)
        except (KeyError, ValueError, TypeError):
            continue
        ts = tx.get("block_timestamp")
        payments.append(
            {
                "txid": tx["transaction_id"],
                "amount": amount,
                "confirmations": 1,  # only_confirmed=true
                "timestamp": int(ts) // 1000 if ts else None,
            }
        )
    return payments


# --- USDC on Solana (SPL token, via RPC token-balance deltas) ---------------
async def fetch_spl_incoming(
    session: aiohttp.ClientSession, owner: str, mint: str
) -> list[dict]:
    accounts = await _solana_rpc(
        session,
        "getTokenAccountsByOwner",
        [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    )
    if not accounts or not accounts.get("value"):
        return []  # buyer's token account doesn't exist until first payment lands

    payments: list[dict] = []
    for acc in accounts["value"]:
        ata = acc["pubkey"]
        sigs = await _solana_rpc(
            session, "getSignaturesForAddress", [ata, {"limit": 15}]
        )
        for sig_info in sigs or []:
            if sig_info.get("err"):
                continue
            tx = await _solana_rpc(
                session,
                "getTransaction",
                [sig_info["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}],
            )
            if not tx:
                continue
            meta = tx.get("meta") or {}
            pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])}
            post = {b["accountIndex"]: b for b in meta.get("postTokenBalances", [])}

            delta = 0.0
            for idx, pb in post.items():
                if pb.get("mint") != mint or pb.get("owner") != owner:
                    continue
                post_amt = float(pb["uiTokenAmount"].get("uiAmount") or 0)
                pre_amt = 0.0
                if idx in pre:
                    pre_amt = float(pre[idx]["uiTokenAmount"].get("uiAmount") or 0)
                delta = post_amt - pre_amt
                break
            if delta <= 0:
                continue
            confirmations = 1 if sig_info.get("confirmationStatus") == "finalized" else 0
            payments.append(
                {
                    "txid": sig_info["signature"],
                    "amount": delta,
                    "confirmations": confirmations,
                    "timestamp": sig_info.get("blockTime"),
                }
            )
    return payments


async def fetch_incoming(
    coin: str, session: aiohttp.ClientSession, meta: dict
) -> list[dict]:
    kind = meta.get("kind")
    address = meta["address"]
    if kind == "ltc":
        return await fetch_ltc_incoming(session, address)
    if kind == "eth":
        return await fetch_eth_incoming(session, address)
    if kind == "sol":
        return await fetch_sol_incoming(session, address)
    if kind == "erc20":
        return await fetch_erc20_incoming(session, address, meta["contract"])
    if kind == "trc20":
        return await fetch_trc20_incoming(session, address, meta["contract"])
    if kind == "spl":
        return await fetch_spl_incoming(session, address, meta["mint"])
    raise ValueError(f"No fetcher for coin {coin} (kind={kind})")
