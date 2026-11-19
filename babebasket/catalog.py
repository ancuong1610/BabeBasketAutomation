"""What the shop sells: product groups, exclusions and the flammability check.

Customers: Vietnamese girls and young women (high school to ~35), self-care consumables.
Groups (daily quota in config.toml [groups]):
  hair  - shampoo, conditioner, masks, leave-in, oils, styling (no aerosols)
  body  - body lotion/cream/oil, deo (roll-on/stick), hand, foot, shower, scrub, bath salt, intimate care
  extra - sun care, skincare and lip care, beauty supplements (skin/hair/nails, weight management),
          gift sets, men's products; must be very well rated (stricter thresholds)
Never: spray cans that carry a fire warning (cannot be shipped to Vietnam; see make_safety_checker),
electrical devices, accessories (brushes, sponges, ...), perfume except Jean & Len hair perfume.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable

# --- exclusions --------------------------------------------------------------------------
EXCLUDE = re.compile(
    r"bürste|kamm\b|haargummi|haarband|haarreif|haarklammer|spange|"
    r"schwamm|sponge|massage|föhn|glätteisen|lockenstab|rasierer|epilierer|elektrisch|feile|raspel|hobel|"
    r"nagellack|zahnpasta|zahnbürste|socken|handschuh|waschhandschuh|pinsel|wattepad|einlegesohle|"
    r"selbstbräuner|bräunungs|brow|augenbraue|muskeln|gelenke|"
    r"chips|schoko|praline|bonbon|lebkuchen|kaffee|fruchtgummi|kerze|\bled\b|lichterkette|deko",
    re.I,
)
PERFUME = re.compile(r"parfum|perfume|eau de |body ?mist|duftspray", re.I)
SUPPLEMENT_CATS = re.compile(r"hautbild|wohlbefinden|vitamine|mineralstoffe|abnehmprodukte|nahrungserg", re.I)
SUPPLEMENT_FORM = re.compile(r"kapseln|tabletten|dragees|pulver|trinkampullen|shot\b|gummies", re.I)
BEAUTY_SUPPLEMENT = re.compile(r"haut|haar|nägel|biotin|kollagen|collagen|hyaluron|beauty|glow|abnehm|figur|sättigung", re.I)

# --- groups (checked in this order) ------------------------------------------------------
GROUP_RULES = [
    ("extra", re.compile(r"geschenkset|geschenk|adventskalender|\bset\b", re.I)),          # gift sets
    ("extra", re.compile(r"für männer|\bmen\b|herren|\bbart", re.I)),                       # men's products
    ("extra", re.compile(f"{SUPPLEMENT_CATS.pattern}|{SUPPLEMENT_FORM.pattern}", re.I)),     # beauty supplements
    ("hair", re.compile(r"shampoo|conditioner|spülung|haarkur|haarmaske|leave-in|haaröl|haarserum|"
                        r"haarstyling|haarpflege|haarparfum|haarmilch|haarwasser|haarpuder|haargel|stylinggel|"
                        r"haarwachs|styling|locken|hitzeschutz|frizz|volumenspray|schaumfestiger", re.I)),
    ("extra", re.compile(r"sonne|\blsf\b|\bspf\b|after sun", re.I)),                         # sun care
    ("body", re.compile(r"bodylotion|körper|hautcreme|deo|handcreme|handmaske|fuß|duschgel|dusch|"
                        r"peeling|body scrub|badesalz|badezusatz|intim", re.I)),
    ("extra", re.compile(r"gesicht|tagescreme|nachtcreme|serum|augen|feuchtigkeitscreme|lippe|pickel|mitesser",
                         re.I)),                                                            # skincare, lip care
]


def classify(product: dict) -> tuple[str | None, str]:
    """(group, reason). group None means the shop does not sell it; reason says why."""
    text = f"{product.get('name', '')} | {product.get('categories', '')}"
    if EXCLUDE.search(text):
        return None, f"excluded type ({EXCLUDE.search(text).group(0)})"
    if re.search(r"adventskalender", text, re.I) and re.search(r",\s*[\d.,]+\s*g\b", product.get("name", "")):
        return None, "food advent calendar (sold by weight)"
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
# Not shippable = BOTH a fire warning AND a pressurised spray can (owner's rule, 2026-11-18).
# A spray without the warning, or a non-spray with one, ships normally.
FIRE_WARNING = re.compile(r"entzündbar|entzündlich|flammable|\bH22[0-9]\b", re.I)
SPRAY_CAN_WARNING = re.compile(r"aerosol|unter druck|druckbehälter", re.I)
SPRAY_CAN_NAME = re.compile(r"spray|aerosol|schaum|mousse|trockenshampoo", re.I)
ROSSMANN_SPRAY = re.compile(r"spray|aerosol|haarschaum|schaumfestiger|rasierschaum", re.I)


def _section_text(dm_detail_raw: dict, header: str) -> str:
    parts = []
    for group in dm_detail_raw.get("descriptionGroups", []):
        if re.search(header, group.get("header", ""), re.I):
            for block in group.get("contentBlock", []):
                parts += block.get("texts", []) + block.get("bulletpoints", [])
                parts += [f"{d.get('title')}: {d.get('description')}" for d in block.get("descriptionList", [])]
    return " ".join(parts)


def warning_text(dm_detail_raw: dict) -> str:
    return _section_text(dm_detail_raw, r"warn|aufbewahrung|sicherheit|gefahr")


def flammable_reason(dm_detail_raw: dict, name: str = "") -> str:
    """Why a dm product cannot be shipped (fire warning AND spray can), or '' if it can."""
    warnings = warning_text(dm_detail_raw)
    fire = FIRE_WARNING.search(warnings)
    if not fire:
        return ""
    can = SPRAY_CAN_WARNING.search(warnings) or SPRAY_CAN_NAME.search(name)
    return f"spray can with fire warning ({fire.group(0)}, {can.group(0)})" if can else ""


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
            reason = flammable_reason(fetch_detail(pid), product.get("name", ""))
            time.sleep(delay)
        else:
            match = ROSSMANN_SPRAY.search(product.get("name", ""))
            reason = f"spray/mousse by name ({match.group(0)})" if match else ""
        conn.execute("INSERT OR REPLACE INTO product_checks VALUES (?, ?, ?, ?, ?)",
                     (store, pid, int(bool(reason)), reason, db.now()))
        conn.commit()
        return not reason, reason

    return check


# --- shampoo + conditioner pairs ---------------------------------------------------------
# Shampoo and conditioner are sold as a pair of the same brand and line ("Shampoo Traube
# Hydraboost" + "Conditioner Traube Hydraboost"); dm lists them apart, so we match them.
TWO_IN_ONE = re.compile(r"\d\s*in\s*1|shampoo\s*&\s*(conditioner|spülung)|(conditioner|spülung)\s*&\s*shampoo", re.I)
SHAMPOO_NAME = re.compile(r"^\s*(haar)?shampoo\b", re.I)
CONDITIONER_NAME = re.compile(r"^\s*(haar)?(conditioner|spülung)\b", re.I)
PARTNER_MIN_RATING = 3.0   # the twin of a good product; the card shows its rating so the owner can judge
LINE_STOP = {"ml", "l", "g", "st", "und", "&", "mit", "für", "x"}


def hair_kind(product: dict) -> str | None:
    """'shampoo', 'conditioner' (rinse-out, not spray), '2in1', or None."""
    name = product.get("name") or ""
    if TWO_IN_ONE.search(name):
        return "2in1"
    if SHAMPOO_NAME.search(name):
        return "shampoo"
    if CONDITIONER_NAME.search(name) and not re.search(r"sprüh|spray|leave", name, re.I):
        return "conditioner"
    return None


def line_words(product: dict) -> list[str]:
    """The product line: the words between the type word and the first comma, e.g. ['traube', 'hydraboost']."""
    name = re.sub(r"^\s*(haar)?(shampoo|conditioner|spülung)\b", "", product.get("name") or "", flags=re.I)
    line = re.sub(r"[-/+]", " ", name.split(",")[0].lower())
    return [w for w in re.findall(r"[\wäöüß]+", line) if w not in LINE_STOP and not w.isdigit()]


def same_line(a: dict, b: dict) -> bool:
    wa, wb = line_words(a), line_words(b)
    if not wa or not wb:
        return False
    if "".join(wa) == "".join(wb):          # "Honig Schätze" == "Honigschätze"
        return True
    sa, sb = set(wa), set(wb)
    return sa <= sb or sb <= sa             # "Full Hair Magic" in "Express Repair Full Hair Magic"


def _brand(p: dict) -> str:
    return re.sub(r"\s+", " ", (p.get("brand") or "").strip().lower())


def find_partner(product: dict, rows: list[dict]) -> dict | None:
    """The matching conditioner for a shampoo (or shampoo for a conditioner): same store, brand and line."""
    kind = hair_kind(product)
    if kind not in ("shampoo", "conditioner"):
        return None
    want = "conditioner" if kind == "shampoo" else "shampoo"
    best, best_rank = None, None
    for other in rows:
        if other.get("product_id") == product.get("product_id") or other.get("store") != product.get("store"):
            continue
        if _brand(other) != _brand(product) or hair_kind(other) != want or other.get("price_eur") is None:
            continue
        if other.get("rating") is not None and other["rating"] < PARTNER_MIN_RATING:
            continue
        if same_line(product, other):
            rank = (line_words(other) == line_words(product), other.get("review_count") or 0)
            if best_rank is None or rank > best_rank:
                best, best_rank = other, rank
    return best
