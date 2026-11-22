"""Cost and suggested sale price, the same chain of formulas as the shop's price sheet
("Bảng tính giá hàng order Đức"). Every number lives in config.toml [pricing].

    rate with buffer   = vnd_per_eur x (1 + fx_buffer)                 30,000 x 1.03 = 30,900
    buy price (VND)    = EUR after coupon x rate with buffer
    cargo              = weight kg x cargo_vnd_per_kg
    base cost          = buy price + cargo + packaging_vnd (+ other_vnd)
    cost               = base cost x (1 + risk)                          the "giá vốn thật"
    suggested price    = cost / (1 - sales_fee - target_margin), rounded UP to round_vnd
    CTV price          = suggested x ctv_share;  deposit = suggested x deposit

The margin is on the sale price (30 % of what the buyer pays), not 30 % added to the cost.
"""

from __future__ import annotations

import math
import re

DEFAULTS = {
    "vnd_per_eur": 30000, "fx_buffer": 0.03, "cargo_vnd_per_kg": 210000, "packaging_vnd": 5000, "other_vnd": 0,
    "risk": 0.05, "sales_fee": 0.05, "target_margin": 0.30, "round_vnd": 1000, "ctv_share": 0.95, "deposit": 0.50,
}

# Shipping weight from the pack size in the product name, fitted on the price sheet's own examples
# (200 ml -> ~250 g, 400 ml -> ~450 g, 75 ml -> ~115 g): bottle and box add about 15 % plus 30 g.
SIZE = re.compile(r"(?:(\d+)\s*x\s*)?(\d+(?:[.,]\d+)*)\s*(ml|l|g|kg)\b", re.I)
SMALL_ITEM_G = 50          # lipstick, lip balm, sachets: the sheet uses 50 g
DEFAULT_WEIGHT_G = 300     # no size in the name


def _number(text: str) -> float:
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", text):  # German thousands: 1.000 ml
        return float(text.replace(".", ""))
    return float(text.replace(",", "."))


def estimate_weight(name: str) -> tuple[int, bool]:
    """(grams, estimated). Always an estimate here; the owner can correct it on the sheet."""
    m = SIZE.search(name or "")
    if not m:
        return DEFAULT_WEIGHT_G, True
    count = int(m.group(1) or 1)
    amount = _number(m.group(2)) * (1000 if m.group(3).lower() in ("l", "kg") else 1)
    content = amount * count
    if content <= 20:
        return SMALL_ITEM_G, True
    factor, tare = (1.8, 20) if re.search(r"öl\b|\boil\b", name, re.I) and content <= 150 else (1.15, 30)  # glass
    return int(math.ceil((content * factor + tare) / 10) * 10), True


def quote(price_eur: float, store: str, cfg: dict, coupon_pct: float | None = None,
          weight_g: float | None = None, name: str = "") -> dict:
    """coupon_pct: a coupon found for this product; the bigger of it and the store's standing coupon counts.

    weight_g: shipping weight; estimated from `name` when not given.
    """
    p = {**DEFAULTS, **{k: v for k, v in cfg.get("pricing", {}).items() if k in DEFAULTS}}
    coupon_pct = max(float(cfg.get("coupons", {}).get(store, 0)), float(coupon_pct or 0))
    cost_eur = round(price_eur * (1 - coupon_pct / 100), 2)
    estimated = weight_g is None
    if estimated:
        weight_g, _ = estimate_weight(name)
    rate = p["vnd_per_eur"] * (1 + p["fx_buffer"])
    base = cost_eur * rate + weight_g / 1000 * p["cargo_vnd_per_kg"] + p["packaging_vnd"] + p["other_vnd"]
    cost = base * (1 + p["risk"])
    step = p["round_vnd"]
    sale = int(math.ceil(cost / (1 - p["sales_fee"] - p["target_margin"]) / step) * step)
    return {
        "shelf_eur": price_eur,
        "coupon_pct": coupon_pct,
        "cost_eur": cost_eur,
        "weight_g": int(weight_g),
        "weight_estimated": estimated,
        "cost_vnd": int(round(cost)),
        "sale_vnd": sale,                                   # "giá bán dự tính"
        "ctv_vnd": int(round(sale * p["ctv_share"] / step) * step),
        "deposit_vnd": int(round(sale * p["deposit"] / step) * step),
    }


def vnd(amount: float) -> str:
    """173000 -> '173.000 đ' (Vietnamese thousands dots)."""
    return f"{int(amount):,} đ".replace(",", ".")
