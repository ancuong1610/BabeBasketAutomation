"""Pick the day's products: per group quota, well-rated, affordable, safe to ship, not drafted recently."""

from __future__ import annotations

import math
import sqlite3
import sys
from typing import Callable

from babebasket import catalog, db

PRIOR_RATING = 4.0   # Bayesian prior: few reviews pull a rating toward this
PRIOR_WEIGHT = 50
DEFAULT_GROUPS = {"hair": {"per_day": 3}, "body": {"per_day": 2}, "extra": {"per_day": 1}}


def score(rating: float, reviews: int, brand: str, preferred: list[str]) -> float:
    adjusted = (PRIOR_RATING * PRIOR_WEIGHT + rating * reviews) / (PRIOR_WEIGHT + reviews)
    s = (adjusted - 3.5) * math.log10(reviews + 10)
    if any(p.lower() in (brand or "").lower() for p in preferred):
        s *= 1.15
    return round(s, 4)


def eligible(product: dict, cfg: dict) -> tuple[str | None, str]:
    """(group, reason). The group if the product passes category, rating and price rules."""
    group, reason = catalog.classify(product)
    if group is None:
        return None, reason
    sc = cfg["shortlist"]
    rules = {**sc, **cfg.get("groups", {}).get(group, {})}
    if (product.get("rating") or 0) < rules["min_rating"] or (product.get("review_count") or 0) < rules["min_reviews"]:
        return None, f"rating below {rules['min_rating']}★/{rules['min_reviews']} reviews for {group}"
    price = product.get("price_eur")
    if price is None:
        return None, "no price"
    cap = sc.get("max_price_gift_set_eur", 40) if catalog.is_gift_set(product) else sc.get("max_price_eur", 20)
    if price > cap:
        return None, f"€{price:.2f} is above €{cap}"
    return group, ""


def pick(conn: sqlite3.Connection, cfg: dict, limit: int | None = None,
         check: Callable[[dict], tuple[bool, str]] | None = None) -> list[dict]:
    sc = cfg["shortlist"]
    groups = cfg.get("groups") or DEFAULT_GROUPS
    check = check or catalog.make_safety_checker(conn)
    skip = db.recently_drafted(conn, sc["repeat_after_days"])

    ranked: dict[str, list[dict]] = {g: [] for g in groups}
    for r in conn.execute("SELECT * FROM products WHERE price_eur IS NOT NULL").fetchall():
        item = dict(r)
        if (item["store"], item["product_id"]) in skip:
            continue
        group, _ = eligible(item, cfg)
        if group not in ranked:
            continue
        s = score(item["rating"], item["review_count"], item["brand"], sc.get("preferred_brands", []))
        if not catalog.is_gift_set(item) and item["price_eur"] > sc.get("sweet_spot_eur", 10):
            s *= 0.75  # most customers are students: dearer products rank lower
        item["score"], item["group"] = round(s, 4), group
        ranked[group].append(item)
    for items in ranked.values():
        items.sort(key=lambda x: x["score"], reverse=True)

    total = limit or sc["per_day"]
    out: list[dict] = []
    per_brand: dict[str, int] = {}

    def take(items: list[dict], quota: int) -> None:
        taken = 0
        for item in items:
            if taken >= quota or len(out) >= total:
                return
            if item in out:
                continue
            brand = (item["brand"] or "").split()[0].lower()
            if per_brand.get(brand, 0) >= 2:  # variety: at most 2 per brand per day
                continue
            try:
                ok, reason = check(item)
            except Exception as err:  # cannot verify: leave it out today
                ok, reason = False, f"safety check failed: {err}"
            if not ok:
                print(f"skip {item['brand']} {item['name'][:40]}: {reason}", file=sys.stderr)
                continue
            per_brand[brand] = per_brand.get(brand, 0) + 1
            out.append(item)
            taken += 1

    for group, rules in groups.items():           # each group's own quota first ...
        take(ranked.get(group, []), rules.get("per_day", 0))
    leftovers = sorted((i for items in ranked.values() for i in items), key=lambda x: x["score"], reverse=True)
    take(leftovers, total)                        # ... then fill any gap with the best of the rest
    return out
