"""babe basket pipeline commands.

    python3 -m babebasket scrape            # dm (+ Rossmann categories in config) -> SQLite + CSV
    python3 -m babebasket shortlist         # show today's picks
    python3 -m babebasket draft             # details + reviews + LLM drafts for the shortlist
    python3 -m babebasket post              # send drafted cards to Discord
    python3 -m babebasket check             # read approvals from Discord
    python3 -m babebasket daily             # scrape + draft + post
    python3 -m babebasket try dm 3050111    # one product end to end, printed (no Discord)
    python3 -m babebasket try dm 3050111 --image   # ... and render the post image
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from babebasket import config, db, llm, pricing, shortlist
from babebasket.config import DATA
from babebasket.stores import dm, rossmann


def cmd_scrape(cfg: dict, conn) -> None:
    all_rows = []
    for category in cfg["dm"]["categories"]:
        rows = dm.crawl_category(category)
        print(f"dm  {category}: {len(rows)} products")
        if not rows:
            print(f"    ! no products: is '{category}' an exact dm category name?", file=sys.stderr)
        all_rows += rows
        time.sleep(dm.DELAY_SECONDS)
    for url in cfg.get("rossmann", {}).get("categories", []):
        try:
            rows = rossmann.crawl_category(url)
        except Exception as err:
            print(f"rossmann {url}: failed: {err}", file=sys.stderr)
            continue
        print(f"rossmann {url}: {len(rows)} products")
        all_rows += rows

    unique = {(r["store"], r["product_id"]): r for r in all_rows}
    db.upsert_products(conn, list(unique.values()))
    path = DATA / f"products_{dt.date.today().isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        dm.write_csv(unique.values(), fh)
    print(f"{len(unique)} products -> {path}")


def cmd_shortlist(cfg: dict, conn) -> list[dict]:
    picks = shortlist.pick(conn, cfg)
    for p in picks:
        q = pricing.quote(p["price_eur"], p["store"], cfg)
        print(f"{p['score']:6.2f}  {p['store']:8} {p['brand'][:20]:20} €{p['price_eur']:<6} → {q['sale_k']}🐠  "
              f"{p['rating']:.2f}★ {p['review_count']:>5}  {p['name'][:50]}")
    return picks


def enrich(product: dict) -> tuple[dict, list[dict]]:
    if product["store"] == "dm":
        detail = dm.fetch_detail(product["product_id"])
        time.sleep(1)
        reviews = dm.fetch_reviews(product["product_id"])
        if detail.get("images"):
            product["image_url"] = detail["images"][0]
        return detail, reviews
    return {"claims": [], "breadcrumbs": [], "description": ""}, []


def make_draft(product: dict, cfg: dict) -> dict:
    detail, reviews = enrich(product)
    quote = pricing.quote(product["price_eur"], product["store"], cfg)
    draft = llm.draft_post(product, detail, reviews, cfg)
    warnings = llm.warnings(draft)  # before the {PRICE} placeholder is filled in
    draft = llm.fill_price(draft, quote["price_text"])
    return {"product": product, "detail": detail, "quote": quote, "draft": draft, "warnings": warnings}


def fetch_bytes(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (babebasket)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def build_image(payload: dict, out, cfg: dict, fetch_bytes=fetch_bytes, gen=None) -> str | None:
    """Render the post image for a draft payload. Returns the file path, or None if there is no packshot.

    Gemini scene first; if it fails or returns something that is not an image, the plain
    fallback background is used. Sets payload["image_path"] and payload["image_source"].
    """
    import io

    from PIL import Image, UnidentifiedImageError

    from babebasket import postimage, scene

    gen = gen or scene.generate
    product = payload["product"]
    if not product.get("image_url"):
        return None
    try:
        packshot = fetch_bytes(product["image_url"])
    except Exception as err:
        print(f"! packshot download failed for {product['product_id']}: {err}", file=sys.stderr)
        return None
    labels = llm.image_labels(payload["draft"])
    source, base = "fallback", None
    generated = gen(packshot, product, cfg)
    if generated:
        try:
            base = Image.open(io.BytesIO(generated))
            base.load()
            source = "gemini"
        except (UnidentifiedImageError, OSError) as err:
            print(f"! scene was not an image for {product['product_id']}: {err}", file=sys.stderr)
            base = None
    if base is None:
        base = postimage.fallback_scene(packshot)
    path = postimage.save(postimage.render(base, labels), out)
    payload["image_path"], payload["image_source"] = str(path), source
    return str(path)


def cmd_draft(cfg: dict, conn, limit: int | None) -> None:
    for product in shortlist.pick(conn, cfg, limit):
        try:
            payload = make_draft(product, cfg)
        except Exception as err:
            print(f"! {product['brand']} {product['name'][:40]}: {err}", file=sys.stderr)
            continue
        draft_id = db.add_draft(conn, product["store"], product["product_id"], payload)
        try:
            if build_image(payload, DATA / "images" / f"draft-{draft_id}.jpg", cfg):
                db.set_payload(conn, draft_id, payload)
        except Exception as err:  # never lose a draft over its image
            print(f"! image for draft #{draft_id}: {err}", file=sys.stderr)
        usage = payload["draft"].get("_usage", {})
        print(f"draft #{draft_id}: {product['brand']} · {product['name'][:50]}  "
              f"({usage.get('total_tokens', '?')} tokens)")


def cmd_post(cfg: dict, conn) -> None:
    from babebasket import discord

    channel = cfg["discord"]["channel_id"]
    if not channel:
        raise SystemExit("Set discord.channel_id in config.toml or DISCORD_CHANNEL_ID in .env")
    for row in db.drafts(conn, "drafted"):
        payload = json.loads(row["payload"])
        n = len(payload["draft"].get("captions", []))
        image = payload.get("image_path")
        image = image if image and Path(image).exists() else None

        def card(with_image: bool) -> dict:
            return discord.build_card(row["id"], payload["product"], payload["quote"], payload["draft"],
                                      payload["warnings"], has_image=with_image)

        try:
            try:
                message_id = discord.post_card(channel, card(bool(image)), n, image_path=image)
            except Exception as err:
                if not image:
                    raise
                print(f"! image upload failed for draft #{row['id']}, sending card without it: {err}", file=sys.stderr)
                message_id = discord.post_card(channel, card(False), n)
        except Exception as err:  # one bad card must not stop the rest of the day's drafts
            print(f"! card for draft #{row['id']} failed: {err}", file=sys.stderr)
            continue
        db.set_status(conn, row["id"], "pending", discord_message_id=message_id)
        print(f"card for draft #{row['id']} -> message {message_id}")
        time.sleep(1)


def cmd_check(cfg: dict, conn) -> None:
    from babebasket import discord

    channel = cfg["discord"]["channel_id"]
    owners = cfg["discord"].get("owner_user_ids", [])
    for row in db.drafts(conn, "pending"):
        payload = json.loads(row["payload"])
        n = len(payload["draft"].get("captions", []))
        decision = discord.read_decision(channel, row["discord_message_id"], n, owners)
        if not decision:
            continue
        db.set_status(conn, row["id"], decision["status"], chosen_caption=decision["caption"], decided_at=db.now())
        if decision["status"] == "approved":
            note = " (several numbers tapped, used the first)" if decision.get("multiple") else ""
            discord.reply(channel, row["discord_message_id"],
                          f"✅ Draft #{row['id']} approved with caption {decision['caption']}{note}. Queued for posting.")
        else:
            discord.reply(channel, row["discord_message_id"], f"❌ Draft #{row['id']} rejected.")
        print(f"draft #{row['id']}: {decision['status']} (caption {decision['caption']})")


def cmd_try(cfg: dict, store: str, product_id: str, image: bool = False) -> None:
    """One product end to end, printed. Needs OPENROUTER_API_KEY; does not touch Discord or the DB."""
    if store != "dm":
        raise SystemExit("try supports dm for now")
    from babebasket.http import request_json

    raw = request_json(dm.DETAIL_URL.format(dan=product_id))
    rating = request_json(dm.RATINGS_URL.format(ids=product_id))[0]
    product = {
        "store": "dm",
        "product_id": product_id,
        "brand": raw.get("brand", {}).get("name", ""),
        "name": raw.get("title", {}).get("headline", ""),
        "price_eur": dm.parse_price(raw.get("price", {}).get("price", {}).get("current", {}).get("value", "")),
        "rating": rating["ratingAvg"],
        "review_count": rating["ratingCount"],
        "categories": "",
        "image_url": "",
        "product_url": dm.SITE + raw.get("self", ""),
    }
    payload = make_draft(product, cfg)
    print(json.dumps({k: payload[k] for k in ("product", "quote", "draft", "warnings")}, ensure_ascii=False, indent=1))
    if image:
        path = build_image(payload, DATA / "images" / f"try-{product_id}.jpg", cfg)
        print(f"image ({payload.get('image_source', 'none')}): {path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="babebasket", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape")
    sub.add_parser("shortlist")
    d = sub.add_parser("draft")
    d.add_argument("--limit", type=int)
    sub.add_parser("post")
    sub.add_parser("check")
    sub.add_parser("daily")
    nt = sub.add_parser("notify", help="post a message to the approval channel")
    nt.add_argument("text")
    t = sub.add_parser("try")
    t.add_argument("store")
    t.add_argument("product_id")
    t.add_argument("--image", action="store_true", help="also render the post image to data/images/")
    args = ap.parse_args(argv)

    cfg = config.load()
    if args.cmd == "notify":
        from babebasket import discord

        discord._call("POST", f"/channels/{cfg['discord']['channel_id']}/messages",
                      {"content": args.text.replace("\\n", "\n")[:1900], "allowed_mentions": {"parse": []}})
        return 0
    if args.cmd == "try":
        cmd_try(cfg, args.store, args.product_id, args.image)
        return 0
    conn = db.connect()
    if args.cmd == "scrape":
        cmd_scrape(cfg, conn)
    elif args.cmd == "shortlist":
        cmd_shortlist(cfg, conn)
    elif args.cmd == "draft":
        cmd_draft(cfg, conn, args.limit)
    elif args.cmd == "post":
        cmd_post(cfg, conn)
    elif args.cmd == "check":
        cmd_check(cfg, conn)
    elif args.cmd == "daily":
        cmd_scrape(cfg, conn)
        cmd_draft(cfg, conn, None)
        cmd_post(cfg, conn)
    return 0
