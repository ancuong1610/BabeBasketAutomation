"""Scrape one dm.de category into a CSV.

Uses dm's own product-search "crawl" endpoint, the same JSON the dm.de
category pages load. The request approach (crawl endpoint, large pages,
backing off on HTTP 429) follows badlogic/heissepreise (MIT licence).

Prices come only from this data, never from an LLM.

Usage:
    python -m scraper.dm --category "Haarkur & Haarmaske" --out data/
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, TextIO

CRAWL_URL = "https://product-search.services.dmtech.com/de/search/crawl"
SITE = "https://www.dm.de"
USER_AGENT = "babebasket-research/0.1 (daily product research, one slow run per day)"
PAGE_SIZE = 200
DELAY_SECONDS = 3.0

FIELDS = [
    "store",
    "dan",
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
        "dan": product.get("dan"),
        "gtin": product.get("gtin"),
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


def fetch_json(url: str, retries: int = 4) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    )
    backoff = 30.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < retries:
                print(f"dm returned 429, waiting {backoff:.0f}s", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def crawl_category(
    category: str,
    fetch: Callable[[str], dict] = fetch_json,
    delay: float = DELAY_SECONDS,
    page_size: int = PAGE_SIZE,
) -> list[dict]:
    scraped_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows: dict[int, dict] = {}
    page = 0
    while True:
        query = urllib.parse.urlencode({"pageSize": page_size, "currentPage": page, "categoryNames": category})
        data = fetch(f"{CRAWL_URL}?{query}")
        for product in data.get("products", []):
            row = parse_product(product, scraped_at)
            rows.setdefault(row["dan"], row)
        page += 1
        if page >= data.get("totalPages", 0):
            break
        time.sleep(delay)
    return list(rows.values())


def write_csv(rows: Iterable[dict], out: TextIO) -> None:
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", default="Haarkur & Haarmaske", help="dm category name as shown on dm.de")
    parser.add_argument("--out", default="data", help="output folder")
    args = parser.parse_args(argv)

    rows = crawl_category(args.category)
    slug = re.sub(r"[^a-z0-9]+", "-", args.category.lower()).strip("-")
    path = Path(args.out) / f"dm_{slug}_{dt.date.today().isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        write_csv(rows, fh)
    print(f"{len(rows)} products -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
