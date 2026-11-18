"""What the shop sells: product groups, exclusions and the flammability check.

Customers: Vietnamese girls and young women (high school to ~35), self-care consumables.
Groups (daily quota in config.toml [groups]):
  hair  - shampoo, conditioner, masks, leave-in, oils, styling (no aerosols)
  body  - body lotion/cream/oil, deo (roll-on/stick), hand, foot, shower, scrub, bath salt, intimate care
  extra - sun care, skincare, beauty supplements (skin/hair/nails, weight management),
          gift sets, men's products; must be very well rated (stricter thresholds)
Never: aerosols / anything with a flammability warning (cannot be shipped to Vietnam),
electrical devices, accessories (brushes, sponges, ...), perfume except Jean & Len hair perfume.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable

# --- exclusions --------------------------------------------------------------------------
EXCLUDE = re.compile(
    r"trockenshampoo|dry shampoo|haarspray|bürste|kamm\b|haargummi|haarband|haarreif|haarklammer|spange|"
    r"schwamm|sponge|massage|föhn|glätteisen|lockenstab|rasierer|epilierer|elektrisch|feile|raspel|hobel|"
    r"nagellack|zahnpasta|zahnbürste|socken|handschuh|waschhandschuh|pinsel|wattepad",
    re.I,
)
PERFUME = re.compile(r"parfum|perfume|eau de |body ?mist|duftspray", re.I)
SUPPLEMENT_CATS = re.compile(r"hautbild|wohlbefinden|vitamine|mineralstoffe|abnehmprodukte|nahrungserg", re.I)
SUPPLEMENT_FORM = re.compile(r"kapseln|tabletten|dragees|pulver|trinkampullen|shot\b|gummies", re.I)
BEAUTY_SUPPLEMENT = re.compile(r"haut|haar|nägel|biotin|kollagen|collagen|hyaluron|beauty|glow|abnehm|figur|sättigung", re.I)

# --- groups (checked in this order) ------------------------------------------------------
GROUP_RULES = [
    ("extra", re.compile(r"geschenkset|geschenk|adventskalender|\bset\b", re.I)),          # gift sets
    ("extra", re.compile(r"für männer|\bmen\b|herren", re.I)),                              # men's products
    ("extra", SUPPLEMENT_CATS),                                                             # beauty supplements
    ("extra", re.compile(r"sonne|\blsf\b|\bspf\b|after sun", re.I)),                         # sun care
    ("hair", re.compile(r"shampoo|conditioner|spülung|haarkur|haarmaske|leave-in|haaröl|haarserum|"
                        r"haarstyling|haarpflege|haarparfum|haarmilch|haarwasser|haarpuder", re.I)),
    ("body", re.compile(r"bodylotion|körper|hautcreme|deo|handcreme|handmaske|fuß|duschgel|dusch|"
                        r"peeling|body scrub|badesalz|badezusatz|intim", re.I)),
    ("extra", re.compile(r"gesicht|tagescreme|nachtcreme|serum|augencreme|feuchtigkeitscreme", re.I)),  # skincare
]


def classify(product: dict) -> tuple[str | None, str]:
    """(group, reason). group None means the shop does not sell it; reason says why."""
    text = f"{product.get('name', '')} | {product.get('categories', '')}"
    if EXCLUDE.search(text):
        return None, f"excluded type ({EXCLUDE.search(text).group(0)})"
    if PERFUME.search(text):
        brand = (product.get("brand") or "").lower().replace(" ", "")
        if "jean" in brand and re.search(r"haarparfum|hair ?perfume", text, re.I):
            return "hair", "Jean & Len hair perfume"
        return None, "perfume (only Jean & Len hair perfume is sold)"
    if SUPPLEMENT_CATS.search(text) or SUPPLEMENT_FORM.search(text):
        if not BEAUTY_SUPPLEMENT.search(text):
            return None, "supplement that is not for skin, hair, nails or figure"
    for group, rule in GROUP_RULES:
        if rule.search(text):
            return group, ""
    return None, "not in the shop's categories"


def is_gift_set(product: dict) -> bool:
    return bool(GROUP_RULES[0][1].search(f"{product.get('name', '')} | {product.get('categories', '')}"))


# --- flammability ------------------------------------------------------------------------
FLAMMABLE = re.compile(r"entzündbar|aerosol|unter druck|\bH22[2-9]\b|flammable", re.I)
ROSSMANN_SPRAY = re.compile(r"spray|aerosol|haarschaum|schaumfestiger|rasierschaum", re.I)


def warning_text(dm_detail_raw: dict) -> str:
    parts = []
    for group in dm_detail_raw.get("descriptionGroups", []):
        if re.search(r"warn|aufbewahrung|sicherheit", group.get("header", ""), re.I):
            for block in group.get("contentBlock", []):
                parts += block.get("texts", []) + block.get("bulletpoints", [])
    return " ".join(parts)


SCHEMA = """CREATE TABLE IF NOT EXISTS product_checks (
    store TEXT NOT NULL, product_id TEXT NOT NULL,
    flammable INTEGER NOT NULL, reason TEXT, checked_at TEXT NOT NULL,
    PRIMARY KEY (store, product_id))"""


def make_safety_checker(conn: sqlite3.Connection, fetch_detail: Callable[[str], dict] | None = None,
                        delay: float = 2.0) -> Callable[[dict], tuple[bool, str]]:
    """Returns check(product) -> (ok, reason). dm: reads the product page's warnings (cached in SQLite).
    Rossmann: product pages cannot be read, so anything named spray/mousse is treated as unsafe."""
    from babebasket import db

    conn.execute(SCHEMA)
    if fetch_detail is None:
        from babebasket.http import request_json
        from babebasket.stores import dm

        def fetch_detail(product_id: str) -> dict:
            return request_json(dm.DETAIL_URL.format(dan=product_id))

    def check(product: dict) -> tuple[bool, str]:
        store, pid = product["store"], str(product["product_id"])
        cached = conn.execute("SELECT flammable, reason FROM product_checks WHERE store = ? AND product_id = ?",
                              (store, pid)).fetchone()
        if cached:
            return not cached[0], cached[1] or ""
        if store == "dm":
            match = FLAMMABLE.search(warning_text(fetch_detail(pid)))
            time.sleep(delay)
        else:
            match = ROSSMANN_SPRAY.search(product.get("name", ""))
        reason = f"flammable/pressurised ({match.group(0)})" if match else ""
        conn.execute("INSERT OR REPLACE INTO product_checks VALUES (?, ?, ?, ?, ?)",
                     (store, pid, int(bool(match)), reason, db.now()))
        conn.commit()
        return not match, reason

    return check
