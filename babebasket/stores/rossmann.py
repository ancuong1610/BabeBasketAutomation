"""rossmann.de: category pages -> products.

Rossmann's robots.txt blocks Claude's crawlers, so this module was written
without fetching rossmann.de from the development session. Run it yourself:

    python3 -m babebasket.stores.rossmann probe                 # once: find categories, save sample pages
    python3 -m babebasket.stores.rossmann probe --keyword haar  # narrow the category search

The probe saves what it fetched to rossmann_probe/ so the parser can be fitted
to the real page structure. Until then `parse_page` reads schema.org JSON-LD,
which may or may not be present.

robots.txt rules this code follows (User-agent: *): no /de/search/, no query
strings except ?pageIndex=N on category pages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from babebasket.config import ROOT
from babebasket.http import USER_AGENT

SITE = "https://www.rossmann.de"
SITEMAP_INDEX = SITE + "/de/sitemap_index.xml"
DELAY_SECONDS = 5.0
MAX_PAGES = 15


def allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.netloc != "www.rossmann.de" or not parts.path.startswith("/de/"):
        return False
    if parts.path.startswith(("/de/search", "/de/cart", "/de/checkout", "/de/my-account")):
        return False
    if parts.query and not re.fullmatch(r"pageIndex=\d+", parts.query):
        return False
    return True


def fetch_text(url: str) -> str:
    if not allowed(url):
        raise ValueError(f"not allowed by robots.txt: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _sitemap_locs(xml: str) -> list[str]:
    return [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]


def _ld_json_blocks(page: str) -> list:
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    return out


def _walk_products(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk_products(x)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _walk_products(node[key])


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def parse_page(page: str, scraped_at: str = "") -> list[dict]:
    rows = []
    for block in _ld_json_blocks(page):
        for p in _walk_products(block):
            offers = p.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            rating = p.get("aggregateRating") or {}
            brand = p.get("brand") or ""
            image = p.get("image") or ""
            url = p.get("url") or offers.get("url") or ""
            rows.append(
                {
                    "store": "rossmann",
                    "product_id": str(p.get("sku") or p.get("productID") or p.get("gtin13") or url),
                    "gtin": str(p.get("gtin13") or p.get("gtin") or ""),
                    "brand": brand.get("name", "") if isinstance(brand, dict) else str(brand),
                    "name": p.get("name", ""),
                    "price_eur": _num(offers.get("price") or offers.get("lowPrice")),
                    "unit_price": "",
                    "rating": _num(rating.get("ratingValue")),
                    "review_count": int(_num(rating.get("reviewCount") or rating.get("ratingCount")) or 0),
                    "categories": p.get("category", "") if isinstance(p.get("category"), str) else "",
                    "image_url": image[0] if isinstance(image, list) else image,
                    "product_url": urllib.parse.urljoin(SITE, url),
                    "scraped_at": scraped_at,
                }
            )
    return rows


def crawl_category(url: str, delay: float = DELAY_SECONDS) -> list[dict]:
    scraped_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows: dict[str, dict] = {}
    for page_index in range(MAX_PAGES):
        page_url = url if page_index == 0 else f"{url}?pageIndex={page_index}"
        found = parse_page(fetch_text(page_url), scraped_at)
        new = [r for r in found if r["product_id"] not in rows]
        for r in new:
            rows[r["product_id"]] = r
        if not new:
            break
        time.sleep(delay)
    return list(rows.values())


def probe(keyword: str, out_dir: Path) -> None:
    """Find category URLs from the sitemap and save one category + one product page for inspection."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "keyword": keyword}

    index = fetch_text(SITEMAP_INDEX)
    sitemaps = _sitemap_locs(index)
    report["sitemaps"] = sitemaps
    print(f"{len(sitemaps)} sitemaps in index")

    category_urls, product_urls = [], []
    for sm in sitemaps:
        time.sleep(1)
        try:
            locs = _sitemap_locs(fetch_text(sm))
        except Exception as err:  # keep going; report what failed
            report.setdefault("errors", []).append(f"{sm}: {err}")
            continue
        print(f"  {sm}: {len(locs)} urls")
        category_urls += [u for u in locs if "/c/" in u]
        product_urls += [u for u in locs if "/p/" in u]
    matching = [u for u in category_urls if keyword.lower() in u.lower()]
    report["category_count"] = len(category_urls)
    report["product_count"] = len(product_urls)
    report["matching_categories"] = matching[:100]
    report["sample_product_urls"] = product_urls[:5]

    samples = {"category": (matching or category_urls)[:1], "product": product_urls[:1]}
    for kind, urls in samples.items():
        for url in urls:
            time.sleep(DELAY_SECONDS)
            page = fetch_text(url)
            (out_dir / f"{kind}.html").write_text(page)
            blocks = _ld_json_blocks(page)
            report[f"{kind}_page"] = {
                "url": url,
                "bytes": len(page),
                "ld_json_types": [b.get("@type") if isinstance(b, dict) else "list" for b in blocks],
                "products_via_ld_json": len(parse_page(page)),
                "data_attributes": sorted(set(re.findall(r'\s(data-[a-z0-9-]*(?:product|price|rating|brand)[a-z0-9-]*)=', page)))[:40],
                "state_blobs": [n for n in ("__NEXT_DATA__", "__NUXT__", "window.__INITIAL_STATE__", "dataLayer") if n in page],
            }
            print(f"  saved {kind} page: {url}")

    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nReport: {out_dir / 'report.json'}")
    print("Commit the rossmann_probe/ folder (or send report.json + the two .html files) so the parser can be fitted.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rossmann scraper tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="inspect rossmann.de structure and save samples")
    p.add_argument("--keyword", default="haar")
    p.add_argument("--out", default=str(ROOT / "rossmann_probe"))
    c = sub.add_parser("crawl", help="scrape one category URL and print a summary")
    c.add_argument("url")
    args = ap.parse_args(argv)
    if args.cmd == "probe":
        probe(args.keyword, Path(args.out))
    else:
        rows = crawl_category(args.url)
        print(json.dumps(rows[:3], ensure_ascii=False, indent=1))
        print(f"{len(rows)} products", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
