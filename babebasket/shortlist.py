"""Pick the day's products: well-rated, much-reviewed, not drafted recently."""

from __future__ import annotations

import math
import sqlite3

from babebasket import db

PRIOR_RATING = 4.0   # Bayesian prior: few reviews pull a rating toward this
PRIOR_WEIGHT = 50


def score(rating: float, reviews: int, brand: str, preferred: list[str]) -> float:
    adjusted = (PRIOR_RATING * PRIOR_WEIGHT + rating * reviews) / (PRIOR_WEIGHT + reviews)
    s = (adjusted - 3.5) * math.log10(reviews + 10)
    if any(p.lower() in (brand or "").lower() for p in preferred):
        s *= 1.15
    return round(s, 4)


def pick(conn: sqlite3.Connection, cfg: dict, limit: int | None = None) -> list[dict]:
    sc = cfg["shortlist"]
    skip = db.recently_drafted(conn, sc["repeat_after_days"])
    rows = conn.execute(
        "SELECT * FROM products WHERE price_eur IS NOT NULL AND rating >= ? AND review_count >= ?",
        (sc["min_rating"], sc["min_reviews"]),
    ).fetchall()
    ranked = []
    for r in rows:
        if (r["store"], r["product_id"]) in skip:
            continue
        item = dict(r)
        item["score"] = score(r["rating"], r["review_count"], r["brand"], sc.get("preferred_brands", []))
        ranked.append(item)
    ranked.sort(key=lambda x: x["score"], reverse=True)

    # Variety: at most 2 products of the same brand per day.
    out, per_brand = [], {}
    for item in ranked:
        key = (item["brand"] or "").split()[0].lower()
        if per_brand.get(key, 0) >= 2:
            continue
        per_brand[key] = per_brand.get(key, 0) + 1
        out.append(item)
        if len(out) >= (limit or sc["per_day"]):
            break
    return out
