"""Tiny SQLite order store.

Orders move through these statuses:
    pending   -> created, waiting for a payment to appear on-chain
    detected  -> a matching tx was seen but doesn't have enough confirmations yet
    paid      -> confirmed; owner has been pinged to deliver the key
    expired   -> went unpaid past ORDER_EXPIRY_MINUTES
    cancelled -> ticket closed before payment

Operations are small and fast, so plain (synchronous) sqlite3 is fine even
inside the async bot.
"""

import logging
import os
import sqlite3
import threading
import time

import config

log = logging.getLogger("database")

DB_PATH = config.DATABASE_PATH
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    # Make sure the directory for the DB exists (e.g. a mounted volume at /data).
    # If it can't be created/used, fall back to the working directory so the bot
    # still starts — it just won't persist orders across redeploys.
    global DB_PATH
    parent = os.path.dirname(DB_PATH)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            fallback = os.path.basename(DB_PATH) or "orders.db"
            log.warning(
                "Cannot use database path %s (is the volume mounted?). "
                "Falling back to ./%s — orders won't persist across redeploys.",
                DB_PATH,
                fallback,
            )
            DB_PATH = fallback

    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                channel_id      INTEGER,
                coin            TEXT    NOT NULL,
                address         TEXT    NOT NULL,
                usd_amount      REAL    NOT NULL,
                expected_amount REAL    NOT NULL,
                product_name    TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                txid            TEXT,
                created_at      INTEGER NOT NULL,
                paid_at         INTEGER
            )
            """
        )
        # Migrate older databases that predate the product_name column.
        cols = [row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "product_name" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN product_name TEXT DEFAULT ''")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                channel_id  INTEGER,
                reason      TEXT,
                status      TEXT    NOT NULL DEFAULT 'open',
                created_at  INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                guild_id     INTEGER NOT NULL,
                member_id    INTEGER NOT NULL,
                inviter_id   INTEGER,
                inviter_name TEXT,
                joined_at    INTEGER NOT NULL,
                PRIMARY KEY (guild_id, member_id)
            )
            """
        )


# --- Invite tracking -------------------------------------------------------
def record_invite(guild_id: int, member_id: int, inviter_id: int, inviter_name: str) -> None:
    now = int(time.time())
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO invites "
            "(guild_id, member_id, inviter_id, inviter_name, joined_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, member_id, inviter_id, inviter_name, now),
        )


def get_invite_record(guild_id: int, member_id: int) -> sqlite3.Row | None:
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM invites WHERE guild_id = ? AND member_id = ?",
            (guild_id, member_id),
        ).fetchone()


def count_invites(guild_id: int, inviter_id: int) -> int:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM invites WHERE guild_id = ? AND inviter_id = ?",
            (guild_id, inviter_id),
        ).fetchone()
    return row[0] if row else 0


# --- Support tickets -------------------------------------------------------
def create_support_ticket(user_id: int, channel_id: int, reason: str) -> int:
    now = int(time.time())
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO support_tickets (user_id, channel_id, reason, status, created_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (user_id, channel_id, reason, now),
        )
        return cur.lastrowid


def get_support_ticket_by_channel(channel_id: int) -> sqlite3.Row | None:
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM support_tickets WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()


def get_open_support_tickets() -> list[sqlite3.Row]:
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM support_tickets WHERE status = 'open'"
        ).fetchall()


def set_support_status(ticket_id: int, status: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE support_tickets SET status = ? WHERE id = ?", (status, ticket_id)
        )


def create_order(
    user_id: int,
    channel_id: int,
    coin: str,
    address: str,
    usd_amount: float,
    expected_amount: float,
    product_name: str = "",
) -> int:
    now = int(time.time())
    with _lock, _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
                (user_id, channel_id, coin, address, usd_amount, expected_amount,
                 product_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, channel_id, coin, address, usd_amount, expected_amount,
             product_name, now),
        )
        return cur.lastrowid


def get_order(order_id: int) -> sqlite3.Row | None:
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()


def get_order_by_channel(channel_id: int) -> sqlite3.Row | None:
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()


def get_active_orders() -> list[sqlite3.Row]:
    """Orders still being watched (pending or detected-but-unconfirmed)."""
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'detected')"
        ).fetchall()


def get_pending_amounts(coin: str) -> set[float]:
    """Expected amounts currently in use for a coin (to keep new ones unique)."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT expected_amount FROM orders "
            "WHERE coin = ? AND status IN ('pending', 'detected')",
            (coin,),
        ).fetchall()
    return {row["expected_amount"] for row in rows}


def get_used_txids() -> set[str]:
    """Tx hashes already matched to an order, so we never double-credit one."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT txid FROM orders WHERE txid IS NOT NULL"
        ).fetchall()
    return {row["txid"] for row in rows}


def set_status(order_id: int, status: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?", (status, order_id)
        )


def mark_detected(order_id: int, txid: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE orders SET status = 'detected', txid = ? WHERE id = ?",
            (txid, order_id),
        )


def mark_paid(order_id: int, txid: str) -> None:
    now = int(time.time())
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE orders SET status = 'paid', txid = ?, paid_at = ? WHERE id = ?",
            (txid, now, order_id),
        )
