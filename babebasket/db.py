"""SQLite state: scraped products and drafts moving through approval."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from babebasket.config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    store TEXT NOT NULL,
    product_id TEXT NOT NULL,
    gtin TEXT, brand TEXT, name TEXT,
    price_eur REAL, unit_price TEXT,
    rating REAL, review_count INTEGER,
    categories TEXT, image_url TEXT, product_url TEXT,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (store, product_id)
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    product_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    -- drafted -> pending (caption card sent) -> approved (caption ok) | rejected
    --   -> image_pending (image card sent) -> ready (image ok) | rejected -> published
    status TEXT NOT NULL DEFAULT 'drafted',
    discord_message_id TEXT,
    chosen_caption INTEGER,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS image_calls (
    at TEXT NOT NULL,
    draft_id INTEGER,
    ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls (
    at TEXT NOT NULL, draft_id INTEGER, stage TEXT NOT NULL, model TEXT, generation_id TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER, cost_usd REAL,
    images_in INTEGER, ok INTEGER NOT NULL, error TEXT
);
"""

PRODUCT_COLS = [
    "store", "product_id", "gtin", "brand", "name", "price_eur", "unit_price",
    "rating", "review_count", "categories", "image_url", "product_url",
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DATA / "babebasket.db") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_products(conn: sqlite3.Connection, rows: list[dict]) -> None:
    ts = now()
    cols = ", ".join(PRODUCT_COLS)
    marks = ", ".join("?" for _ in PRODUCT_COLS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in PRODUCT_COLS[2:])
    conn.executemany(
        f"INSERT INTO products ({cols}, first_seen, last_seen) VALUES ({marks}, ?, ?) "
        f"ON CONFLICT(store, product_id) DO UPDATE SET {updates}, last_seen=excluded.last_seen",
        [[row.get(c) for c in PRODUCT_COLS] + [ts, ts] for row in rows],
    )
    conn.commit()


def add_draft(conn: sqlite3.Connection, store: str, product_id: str, payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO drafts (store, product_id, created_at, payload) VALUES (?, ?, ?, ?)",
        (store, product_id, now(), json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def drafts(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM drafts WHERE status = ? ORDER BY id", (status,)).fetchall()


def set_status(conn: sqlite3.Connection, draft_id: int, status: str, **fields) -> None:
    sets = ["status = ?"] + [f"{k} = ?" for k in fields]
    conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id = ?", (status, *fields.values(), draft_id))
    conn.commit()


def recently_drafted(conn: sqlite3.Connection, days: int) -> set[tuple[str, str]]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT store, product_id, payload FROM drafts WHERE created_at >= ?", (cutoff,)).fetchall()
    out = {(r["store"], r["product_id"]) for r in rows}
    for r in rows:  # the conditioner of a shampoo + conditioner pair counts as drafted too
        try:
            pair = (json.loads(r["payload"]).get("product") or {}).get("pair")
        except (ValueError, AttributeError):
            pair = None
        if pair:
            out.add((pair.get("store", r["store"]), str(pair.get("product_id"))))
    return out


def set_payload(conn: sqlite3.Connection, draft_id: int, payload: dict) -> None:
    conn.execute("UPDATE drafts SET payload = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), draft_id))
    conn.commit()


def log_image_call(conn: sqlite3.Connection, draft_id: int | None, ok: bool) -> None:
    """One paid AI image request (successful or not)."""
    conn.execute("INSERT INTO image_calls (at, draft_id, ok) VALUES (?, ?, ?)", (now(), draft_id, int(ok)))
    conn.commit()


def image_calls_today(conn: sqlite3.Connection) -> int:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM image_calls WHERE at >= ?", (today,)).fetchone()[0]


def image_calls_for(conn: sqlite3.Connection, draft_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM image_calls WHERE draft_id = ?", (draft_id,)).fetchone()[0]
