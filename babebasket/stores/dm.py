"""dm.de: category listings, product details and reviews.

Uses the JSON endpoints dm.de's own pages call (dm.de product pages carry no
product JSON-LD). The crawl approach (large pages, backing off on 429)
follows badlogic/heissepreise (MIT licence).

Prices come only from this data, never from an LLM.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import urllib.parse
from typing import Callable

from babebasket.http import request_json

CRAWL_URL = "https://product-search.services.dmtech.com/de/search/crawl"
DETAIL_URL = "https://products.dm.de/product/products/detail/DE/dan/{dan}"
RATINGS_URL = "https://stars.services.dmtech.com/api/DE/v3/ratings/{ids}/summary"
REVIEWS_URL = "https://stars.services.dmtech.com/api/DE/v3/reviews/{dan}"
SITE = "https://www.dm.de"
PAGE_SIZE = 200
DELAY_SECONDS = 10.0  # dm answers 429 quickly; stay slow

FIELDS = [
    "store",
    "product_id",
    "gtin",
    "brand",
    "name",
    "price_eur",
    "unit_price",
    "rating",
    "review_count",
    "categories",
    "image_url",
    "product_url",
    "scraped_at",
]


def parse_price(text: str) -> float | None:
    """'1.234,50 €' -> 1234.5"""
    match = re.search(r"\d[\d.]*,\d{2}", text or "")
    if not match:
        return None
    return float(match.group(0).replace(".", "").replace(",", "."))


def large_image(url: str) -> str:
    """Swap dm's tile-sized image transform for the 1200px zoom version."""
    return re.sub(r"h_\d+,w_\d+", "h_1200,w_1200", url, count=1)


def parse_product(product: dict, scraped_at: str = "") -> dict:
    tile = product.get("tileData", {})
    tracking = tile.get("trackingData", {})
    rating = (product.get("context") or {}).get("rating") or {}
    price_block = tile.get("price", {})

    price = tracking.get("price")
    if price is None:
        price = parse_price(price_block.get("price", {}).get("current", {}).get("value", ""))

    image = (tile.get("heroImage") or (tile.get("images") or [{}])[0]).get("tileSrc", "")

    return {
        "store": "dm",
        "product_id": str(product.get("dan")),
        "gtin": str(product.get("gtin") or ""),
        "brand": product.get("brandName") or tile.get("brand", {}).get("name", ""),
        "name": product.get("title") or tile.get("title", {}).get("tileHeadline", ""),
        "price_eur": float(price) if price is not None else None,
        "unit_price": "; ".join(price_block.get("tileInfos", [])),
        "rating": float(rating["ratingValue"]) if "ratingValue" in rating else None,
        "review_count": int(rating.get("ratingCount", 0)),
        "categories": "; ".join(tracking.get("categories", [])),
        "image_url": large_image(image) if image else "",
        "product_url": SITE + tile["self"] if tile.get("self") else "",
        "scraped_at": scraped_at,
    }


def crawl_category(
    category: str,
    fetch: Callable[[str], dict] = request_json,
    delay: float = DELAY_SECONDS,
    page_size: int = PAGE_SIZE,
) -> list[dict]:
    """All products in one dm category (as named in dm.de's category filter)."""
    scraped_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows: dict[str, dict] = {}
    page = 0
    while True:
        query = urllib.parse.urlencode({"pageSize": page_size, "currentPage": page, "categoryNames": category})
        data = fetch(f"{CRAWL_URL}?{query}")
        for product in data.get("products", []):
            row = parse_product(product, scraped_at)
            rows.setdefault(row["product_id"], row)
        page += 1
        if page >= data.get("totalPages", 0):
            break
        time.sleep(delay)
    return list(rows.values())


def parse_detail(data: dict) -> dict:
    """Official claims and texts from a product detail record."""
    bullets, texts, sections = [], [], {}
    for group in data.get("descriptionGroups", []):
        header = group.get("header", "")
        for block in group.get("contentBlock", []):
            if header == "Produktbeschreibung":
                bullets += block.get("bulletpoints", [])
                texts += [t for t in block.get("texts", []) if not t.startswith(("dm-Artikelnummer", "GTIN"))]
            else:
                parts = block.get("texts", []) + [
                    f"{d.get('title')}: {d.get('description')}" for d in block.get("descriptionList", [])
                ]
                if parts:
                    sections.setdefault(header, []).extend(parts)
    price = data.get("price", {})
    return {
        "claims": bullets,
        "description": "\n".join(texts),
        "features": sections.get("Produktmerkmale", []),
        "usage": "\n".join(sections.get("Verwendungshinweise", [])),
        "warnings": "\n".join(sections.get("Warnhinweise", [])),
        "breadcrumbs": data.get("breadcrumbs", []),
        "price_note": (price.get("notIncreasedSince") or {}).get("text", ""),
        "images": [img.get("zoomSrc") for img in data.get("images", []) if img.get("zoomSrc")],
    }


def fetch_detail(product_id: str, fetch: Callable[[str], dict] = request_json) -> dict:
    return parse_detail(fetch(DETAIL_URL.format(dan=product_id)))


def parse_reviews(data: dict) -> list[dict]:
    out = []
    for review in data.get("reviews", []):
        reviewer, helpful = [], 0
        for module in review.get("modules", []):
            if module.get("type") == "iconLabel":
                reviewer += [f"{d['title']}: {d['label']}" for d in module["data"].get("data", []) if d.get("title")]
            if module.get("type") == "helpful":
                helpful = module["data"].get("positiveFeedbackCount", 0)
        out.append(
            {
                "rating": review.get("rating"),
                "title": review.get("title", ""),
                "text": review.get("reviewText", ""),
                "reviewer": "; ".join(reviewer),
                "helpful": helpful,
                "incentivized": review.get("isIncentivized", False),
            }
        )
    return out


def fetch_reviews(product_id: str, limit: int = 15, fetch: Callable[[str], dict] = request_json) -> list[dict]:
    """Reviews in dm's default "relevance" order."""
    query = urllib.parse.urlencode({"offset": 0, "limit": limit, "sortation": "relevance"})
    return parse_reviews(fetch(f"{REVIEWS_URL.format(dan=product_id)}?{query}"))


def write_csv(rows, out) -> None:
    import csv

    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
