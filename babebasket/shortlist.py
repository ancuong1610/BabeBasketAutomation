"""Pick the day's products: per group quota, well-rated, affordable, safe to ship, not drafted recently."""

from __future__ import annotations

import math
import re
import sqlite3
import sys
from typing import Callable

from babebasket import catalog, coupons, db

PRIOR_RATING = 4.0   # Bayesian prior: few reviews pull a rating toward this
PRIOR_WEIGHT = 50
DEFAULT_GROUPS = {"hair": {"per_day": 3}, "body": {"per_day": 2}, "extra": {"per_day": 1}}
TRUSTED_BRAND_RATING = 4.4   # a brand whose rated products average this may get a new, unrated product picked
TRUSTED_BRAND_MIN_PRODUCTS = 3


def variant_key(product: dict) -> str:
    """Brand + the first 3 words of the name: shades/scents of one product share it
    ('Lippenbalsam Lifter Glaze Oil Balm 001 …' and '… 010 …')."""
    words = re.findall(r"[^\W\d_]+", (product.get("name") or "").lower())[:3]
    return f"{(product.get('brand') or '').strip().lower()}|{' '.join(words)}"


def is_unrated(product: dict) -> bool:
    return not product.get("review_count") or product.get("rating") is None


def brand_ratings(rows: list[dict]) -> dict[str, float]:
    """Average rating per brand over its products with at least 10 reviews (brands with 3+ such products)."""
    by_brand: dict[str, list[float]] = {}
    for r in rows:
        if not is_unrated(r) and r["review_count"] >= 10:
            by_brand.setdefault((r.get("brand") or "").strip().lower(), []).append(r["rating"])
    return {b: sum(v) / len(v) for b, v in by_brand.items() if len(v) >= TRUSTED_BRAND_MIN_PRODUCTS}


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
    if is_unrated(product):
        return None, "no rating yet (only as the daily new-product pick)"
    if product["rating"] < rules["min_rating"] or product["review_count"] < rules["min_reviews"]:
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
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM products WHERE price_eur IS NOT NULL").fetchall()]
    trusted = brand_ratings(all_rows)
    valid_coupons = coupons.active(conn)
    coupon_weight = sc.get("coupon_weight", 2.0)

    def with_coupon(item: dict) -> float:
        """Attach the best valid coupon; product/brand coupons boost the score (20 % -> x1.4 at weight 2)."""
        best = coupons.best_for(item, valid_coupons)
        if not best:
            return 1.0
        item["coupon"] = coupons.summary(best)
        return 1.0 if best["applies_to"] == "all" else 1.0 + coupon_weight * coupons.effective_pct(best) / 100
    fresh: list[dict] = []   # new products with no rating yet, from brands that rate well: the daily "gacha" pick
    for item in all_rows:
        if (item["store"], item["product_id"]) in skip:
            continue
        if is_unrated(item):
            group, _ = catalog.classify(item)
            brand_avg = trusted.get((item.get("brand") or "").strip().lower(), 0)
            cap = sc.get("max_price_gift_set_eur", 40) if catalog.is_gift_set(item) else sc.get("max_price_eur", 20)
            if group in ranked and brand_avg >= TRUSTED_BRAND_RATING and item["price_eur"] <= cap:
                item.update(score=round(brand_avg * with_coupon(item), 4), group=group, is_new=True,
                            brand_rating=round(brand_avg, 2))
                fresh.append(item)
            continue
        group, _ = eligible(item, cfg)
        if group not in ranked:
            continue
        s = score(item["rating"], item["review_count"], item["brand"], sc.get("preferred_brands", []))
        if not catalog.is_gift_set(item) and item["price_eur"] > sc.get("sweet_spot_eur", 10):
            s *= 0.75  # most customers are students: dearer products rank lower
        s *= with_coupon(item)
        item["score"], item["group"] = round(s, 4), group
        ranked[group].append(item)
    for items in ranked.values():
        items.sort(key=lambda x: x["score"], reverse=True)

    total = limit or sc["per_day"]
    out: list[dict] = []
    per_brand: dict[str, int] = {}
    used: set[tuple[str, str]] = set()   # products already in today's list, alone or as half of a pair
    variants: set[str] = set()           # one shade/scent per product per day

    def with_partner(item: dict) -> tuple[dict | None, str]:
        """Shampoo and conditioner go out as a matching pair when one exists (owner's preference);
        with no usable twin the product is sold on its own."""
        if catalog.hair_kind(item) not in ("shampoo", "conditioner"):
            return item, ""
        partner = catalog.find_partner(item, all_rows)
        if partner is None:
            return item, ""
        key = (partner["store"], partner["product_id"])
        if key in skip or key in used:
            return item, ""
        try:
            ok, _ = check(partner)
        except Exception:
            ok = False
        if not ok:
            return item, ""
        shampoo, conditioner = (item, partner) if catalog.hair_kind(item) == "shampoo" else (partner, item)
        post = dict(shampoo, score=item["score"], group=item["group"])
        post["pair"] = {k: conditioner.get(k) for k in (
            "store", "product_id", "gtin", "brand", "name", "price_eur", "unit_price", "rating",
            "review_count", "categories", "image_url", "product_url")}
        return post, ""

    def take(items: list[dict], quota: int) -> None:
        taken = 0
        for item in items:
            if taken >= quota or len(out) >= total:
                return
            if (item["store"], item["product_id"]) in used or variant_key(item) in variants:
                continue
            brand = (item["brand"] or "").split()[0].lower()
            if per_brand.get(brand, 0) >= 2:  # variety: at most 2 per brand per day
                continue
            try:
                ok, reason = check(item)
            except Exception as err:  # cannot verify: leave it out today
                ok, reason = False, f"safety check failed: {err}"
            post = item
            if ok:
                post, reason = with_partner(item)
                ok = post is not None
            if not ok:
                print(f"skip {item['brand']} {item['name'][:40]}: {reason}", file=sys.stderr)
                continue
            per_brand[brand] = per_brand.get(brand, 0) + 1
            out.append(post)
            used.add((post["store"], post["product_id"]))
            variants.add(variant_key(post))
            if post.get("pair"):
                used.add((post["pair"]["store"], post["pair"]["product_id"]))
            taken += 1

    fresh.sort(key=lambda x: x["score"], reverse=True)
    take(fresh, sc.get("new_per_day", 1))         # the new-product pick (if a trusted brand has one) ...
    for group, rules in groups.items():           # ... each group's own quota (a new pick uses its group's slot) ...
        already = sum(1 for p in out if p.get("group") == group)
        take(ranked.get(group, []), max(0, rules.get("per_day", 0) - already))
    leftovers = sorted((i for items in ranked.values() for i in items), key=lambda x: x["score"], reverse=True)
    take(leftovers, total)                        # ... then fill any gap with the best of the rest
    return out
