"""Cost and sale price. Deliberately simple for now; the rule lives in config.toml [pricing]."""

from __future__ import annotations

import math


def quote(price_eur: float, store: str, cfg: dict, coupon_pct: float | None = None) -> dict:
    """coupon_pct: a coupon found for this product; the bigger of it and the store's standing coupon counts."""
    p = cfg["pricing"]
    coupon_pct = max(float(cfg.get("coupons", {}).get(store, 0)), float(coupon_pct or 0))
    cost_eur = round(price_eur * (1 - coupon_pct / 100), 2)
    cost_vnd = cost_eur * p["vnd_per_eur"] + p.get("shipping_vnd_per_item", 0)
    sale_vnd = cost_vnd * (1 + p["markup"])
    step = p.get("round_to_thousand", 5)
    sale_k = int(math.ceil(sale_vnd / 1000 / step) * step)
    return {
        "shelf_eur": price_eur,
        "coupon_pct": coupon_pct,
        "cost_eur": cost_eur,
        "cost_k": round(cost_vnd / 1000),
        "sale_k": sale_k,
        "margin_k": sale_k - round(cost_vnd / 1000),
        "price_text": f"{sale_k}🐠",
    }
