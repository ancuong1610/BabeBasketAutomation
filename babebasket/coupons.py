"""Store coupons from the #coupons Discord channel.

The owner posts screenshots of the dm (or Rossmann) app's coupon tab, or a short text such as
"dm 20% Balea đến 30.11", in #coupons. On every `check` the text model reads new posts, the
coupons are saved with their end date, and the bot replies with what it understood. A ❌ on
that reply deletes those coupons. While a coupon is valid, products it covers rank higher
(shortlist) and the caption card shows it with the cost after the coupon.

The app coupons sit behind the owner's login, so they are never scraped.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import sys
import urllib.request
from typing import Callable

from babebasket import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,              -- dm | rossmann
    applies_to TEXT NOT NULL,         -- all | brands | products | categories
    brands TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    percent REAL,
    payback_multiplier REAL,
    min_spend_eur REAL,
    valid_until TEXT,                 -- YYYY-MM-DD, last day the coupon works
    label TEXT,
    source_message_id TEXT,
    reply_message_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

PAYBACK_POINT_PCT = 0.5   # 1 PAYBACK point per €2 spent, 1 point = 1 cent: "10-fach" is worth 5 %
DELETE = "❌"

EXTRACT_PROMPT = """You read coupons for a German drugstore reseller. The input is one or more screenshots
of the dm or Rossmann app's coupon tab and/or a short note from the owner (Vietnamese or German),
e.g. "dm 20% Balea đến 30.11". Today is {today}.
List EVERY coupon you can see. Return only JSON:
{{"coupons": [{{
  "store": "dm" or "rossmann" (from the logo/app style or the note; if unsure use "{default_store}"),
  "applies_to": "all" (whole purchase) | "brands" | "products" | "categories",
  "brands": ["brand names exactly as on the coupon, e.g. Balea"],
  "keywords": ["German words that appear in the names or categories of the covered products,
               e.g. for 'Haarpflege': Shampoo, Spülung, Conditioner, Haarkur, Haaröl; for one product: its main name words"],
  "percent": number or null (e.g. 20 for "20 %"),
  "payback_multiplier": number or null (e.g. 10 for "10-fach PAYBACK °Punkte"),
  "min_spend_eur": number or null (e.g. "ab 10 € Einkaufswert"),
  "valid_until": "YYYY-MM-DD" or null (the last day it is valid; "gültig bis 30.11." means {year}-11-30),
  "label": "short Vietnamese label, e.g. 'Balea −20%' or 'Haarpflege 10-fach PAYBACK'"
}}]}}
Skip things that are not discounts (e.g. free samples without a discount). Never invent a coupon."""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def today() -> dt.date:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("Europe/Berlin")).date()


def effective_pct(c: dict) -> float:
    pct = c.get("percent") or 0
    if c.get("payback_multiplier"):
        pct = max(pct, c["payback_multiplier"] * PAYBACK_POINT_PCT)
    return float(pct)


# --- matching ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", "", (text or "").lower())


def matches(coupon: dict, product: dict) -> bool:
    if coupon.get("store") not in (product.get("store"), "any"):
        return False
    kind = coupon.get("applies_to")
    if kind == "all":
        return True
    haystack = f"{product.get('brand', '')} {product.get('name', '')} {product.get('categories', '')}"
    flat = _norm(haystack)
    brands = [b for b in coupon.get("brands") or [] if len(_norm(b)) >= 3]
    keywords = [k for k in coupon.get("keywords") or [] if len(_norm(k)) >= 4]
    brand_ok = any(_norm(b) in _norm(product.get("brand", "")) or _norm(b) in flat for b in brands)
    keyword_ok = any(re.search(re.escape(k), haystack, re.I) for k in keywords)
    if kind == "brands":
        # "Balea" alone covers all Balea products; "Balea Haarpflege" needs both
        return brand_ok and (keyword_ok or not keywords)
    if kind in ("products", "categories"):
        return keyword_ok and (brand_ok or not brands)
    return False


def active(conn: sqlite3.Connection, on: dt.date | None = None) -> list[dict]:
    ensure(conn)
    day = (on or today()).isoformat()
    rows = conn.execute(
        "SELECT * FROM coupons WHERE active = 1 AND (valid_until IS NULL OR valid_until >= ?)", (day,)).fetchall()
    out = []
    for r in rows:
        c = dict(r)
        c["brands"], c["keywords"] = json.loads(c["brands"]), json.loads(c["keywords"])
        out.append(c)
    return out


def best_for(product: dict, coupons: list[dict]) -> dict | None:
    """The biggest valid coupon that covers this product (product-specific coupons only; whole-purchase
    coupons apply to everything and so do not change the ranking, but still lower the cost)."""
    hits = [c for c in coupons if matches(c, product)]
    return max(hits, key=effective_pct) if hits else None


def summary(c: dict) -> dict:
    return {"id": c.get("id"), "label": c.get("label") or f"−{effective_pct(c):g}%",
            "percent": effective_pct(c), "valid_until": c.get("valid_until"), "applies_to": c.get("applies_to")}


# --- reading the channel ----------------------------------------------------------------
def _date(value) -> str | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def extract(images: list[bytes], note: str, cfg: dict, post: Callable[..., dict] | None = None,
            default_store: str = "dm") -> list[dict]:
    from babebasket import scene
    from babebasket.http import request_json

    day = today()
    prompt = EXTRACT_PROMPT.format(today=day.isoformat(), year=day.year, default_store=default_store)
    if note:
        prompt += f"\n\nOwner's note: {note[:1000]}"
    data = scene._vision_json(prompt, images, cfg, post or request_json, None)
    out = []
    for c in data.get("coupons") or []:
        if not isinstance(c, dict):
            continue
        c["store"] = c.get("store") if c.get("store") in ("dm", "rossmann") else default_store
        c["applies_to"] = c.get("applies_to") if c.get("applies_to") in ("all", "brands", "products", "categories") \
            else ("brands" if c.get("brands") else "products")
        c["valid_until"] = _date(c.get("valid_until"))
        if effective_pct(c) <= 0:
            continue
        out.append(c)
    return out


def save(conn: sqlite3.Connection, coupons: list[dict], source_message_id: str) -> list[int]:
    ensure(conn)
    ids = []
    for c in coupons:
        cur = conn.execute(
            "INSERT INTO coupons (store, applies_to, brands, keywords, percent, payback_multiplier, min_spend_eur,"
            " valid_until, label, source_message_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c["store"], c["applies_to"], json.dumps(c.get("brands") or [], ensure_ascii=False),
             json.dumps(c.get("keywords") or [], ensure_ascii=False), c.get("percent"), c.get("payback_multiplier"),
             c.get("min_spend_eur"), c.get("valid_until"), c.get("label"), source_message_id, db.now()))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def describe(c: dict) -> str:
    until = f" (đến {dt.date.fromisoformat(c['valid_until']).strftime('%d.%m')})" if c.get("valid_until") else " (không rõ hạn)"
    spend = f", từ €{c['min_spend_eur']:g}" if c.get("min_spend_eur") else ""
    who = {"all": "cả hoá đơn", "brands": ", ".join(c.get("brands") or []),
           "products": ", ".join(c.get("keywords") or [])[:60], "categories": ", ".join(c.get("keywords") or [])[:60]}
    return f"• {c['store']} · {c.get('label') or ''} · {who.get(c['applies_to'], '')}{spend}{until}"


def _kv(conn, key, value=None):
    ensure(conn)
    if value is None:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
    conn.commit()


def find_channel(cfg: dict, conn: sqlite3.Connection) -> str | None:
    """[discord] coupons_channel_id, or the channel named 'coupons' in the approval channel's server."""
    from babebasket import discord

    if cfg.get("discord", {}).get("coupons_channel_id"):
        return str(cfg["discord"]["coupons_channel_id"])
    cached = _kv(conn, "coupons_channel_id")
    if cached:
        return cached
    guild = (discord._call("GET", f"/channels/{cfg['discord']['channel_id']}") or {}).get("guild_id")
    if not guild:
        return None
    for ch in discord._call("GET", f"/guilds/{guild}/channels") or []:
        if ch.get("type") == 0 and "coupon" in (ch.get("name") or "").lower():
            _kv(conn, "coupons_channel_id", ch["id"])
            return ch["id"]
    return None


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "DiscordBot (https://localhost, 0.1)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def process_channel(cfg: dict, conn: sqlite3.Connection, fetch: Callable[[str], bytes] = download,
                    post: Callable[..., dict] | None = None) -> None:
    """Read new owner posts in #coupons, save the coupons, reply; delete coupons whose reply got ❌."""
    from babebasket import discord

    ensure(conn)
    channel = find_channel(cfg, conn)
    if not channel:
        return
    owners = {str(o) for o in cfg["discord"].get("owner_user_ids", [])}

    # ❌ on a bot reply deletes the coupons it listed
    for (reply_id,) in conn.execute(
            "SELECT DISTINCT reply_message_id FROM coupons WHERE active = 1 AND reply_message_id IS NOT NULL").fetchall():
        if DELETE in discord.owner_taps(channel, reply_id, [DELETE], list(owners)):
            conn.execute("UPDATE coupons SET active = 0 WHERE reply_message_id = ?", (reply_id,))
            conn.commit()
            discord.reply(channel, reply_id, "🗑️ Đã xoá các coupon này.")

    last = _kv(conn, "coupons_last_message_id")
    query = f"?after={last}&limit=50" if last else "?limit=20"
    messages = sorted(discord._call("GET", f"/channels/{channel}/messages{query}") or [], key=lambda m: int(m["id"]))
    for m in messages:
        _kv(conn, "coupons_last_message_id", m["id"])
        author = m.get("author") or {}
        if author.get("bot") or (owners and str(author.get("id")) not in owners):
            continue
        images = [fetch(a["url"]) for a in m.get("attachments") or []
                  if (a.get("content_type") or "").startswith("image/") and a.get("url")]
        note = (m.get("content") or "").strip()
        if not images and not note:
            continue
        default_store = "rossmann" if re.search(r"rossmann", note, re.I) else "dm"
        try:
            found = extract(images, note, cfg, post=post, default_store=default_store)
        except Exception as err:
            print(f"! coupons: could not read message {m['id']}: {err}", file=sys.stderr)
            discord.reply(channel, m["id"], "⚠️ Không đọc được coupon trong tin này, bạn gửi lại giúp nhé.")
            continue
        if not found:
            discord.reply(channel, m["id"], "🤔 Không thấy coupon giảm giá nào trong tin này.")
            continue
        ids = save(conn, found, m["id"])
        text = f"🎟️ Đã lưu {len(found)} coupon:\n" + "\n".join(describe(c) for c in found) \
            + f"\nBấm {DELETE} trên tin này nếu bot đọc sai, các coupon trên sẽ bị xoá."
        message = discord._call("POST", f"/channels/{channel}/messages", {
            "content": text[:1900], "message_reference": {"message_id": m["id"], "fail_if_not_exists": False},
            "allowed_mentions": {"parse": []}})
        discord._react(channel, message["id"], [DELETE])
        conn.executemany("UPDATE coupons SET reply_message_id = ? WHERE id = ?", [(message["id"], i) for i in ids])
        conn.commit()
        print(f"coupons: saved {len(ids)} from message {m['id']}")
