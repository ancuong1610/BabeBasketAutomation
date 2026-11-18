"""babe basket pipeline commands.

    python3 -m babebasket scrape            # dm (+ Rossmann categories in config) -> SQLite + CSV
    python3 -m babebasket shortlist         # show today's picks
    python3 -m babebasket draft             # details + reviews + LLM drafts for the shortlist
    python3 -m babebasket post              # send drafted cards to Discord
    python3 -m babebasket check             # read caption + image approvals, make images, publish ready posts
    python3 -m babebasket publish           # publish approved drafts to Threads ([threads].enabled)
    python3 -m babebasket daily             # scrape + draft + post
    python3 -m babebasket try dm 3050111    # one product end to end, printed (no Discord)
    python3 -m babebasket try dm 3050111 --image   # ... and render the post image
    python3 -m babebasket try rossmann first --image  # first product of a saved Rossmann page
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
    warnings = llm.warnings(draft, had_reviews=bool(reviews))  # before the {PRICE} placeholder is filled in
    draft = llm.fill_price(draft, quote["price_text"])
    return {"product": product, "detail": detail, "quote": quote, "draft": draft, "warnings": warnings}


def fetch_bytes(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (babebasket)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def build_image(payload: dict, out, cfg: dict, fetch_bytes=fetch_bytes, gen=None, ai: bool = True,
                feedback: str = "", previous: bytes | None = None) -> str | None:
    """Render the post image for a draft payload. Returns the file path, or None if there is no packshot.

    With ai=True: Gemini scene first; if it fails or returns something that is not an image,
    the plain fallback background is used. With ai=False: plain background only (free).
    `feedback` (+ the `previous` AI scene) asks the model to fix that scene instead of starting over.
    Sets payload["image_path"], payload["image_source"] and, for AI images, payload["scene_path"]
    (the scene without text, kept so a later fix can edit it).
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
    extra = {k: v for k, v in (("feedback", feedback), ("previous", previous)) if v}
    generated = gen(packshot, product, cfg, **extra) if ai else None
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
    if source == "gemini":
        scene_path = Path(out).with_name(Path(out).stem + "-scene.png")
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        base.save(scene_path, "PNG")
        payload["scene_path"] = str(scene_path)
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
        # No image yet: the post image is made only after the caption is approved (see cmd_images).
        draft_id = db.add_draft(conn, product["store"], product["product_id"], payload)
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
                          f"✅ Draft #{row['id']} approved with caption {decision['caption']}{note}. Making the post image…")
        else:
            discord.reply(channel, row["discord_message_id"], f"❌ Draft #{row['id']} rejected.")
        print(f"draft #{row['id']}: {decision['status']} (caption {decision['caption']})")


def ai_tries_left(cfg: dict, conn, draft_id: int) -> int:
    """Paid AI image requests still allowed for this draft, within the daily cap."""
    ic = cfg.get("image", {})
    if not ic.get("enabled", True):
        return 0
    per_post = ic.get("max_per_post", 2) - db.image_calls_for(conn, draft_id)
    per_day = ic.get("max_per_day", 4) - db.image_calls_today(conn)
    return max(0, min(per_post, per_day))


def send_image_card(cfg: dict, conn, row, payload: dict, ai: bool, note: str = "", feedback: str = "") -> None:
    """Make the post image (AI if allowed, else plain) and send it to Discord for approval.

    With `feedback`, the previous AI scene (if any) is edited as asked instead of starting over.
    """
    from babebasket import discord

    draft_id = row["id"]
    notes = [note] if note else []
    use_ai = ai and ai_tries_left(cfg, conn, draft_id) > 0
    if ai and not use_ai:
        notes.append("AI image limit reached (see [image] in config.toml), so this is the plain background.")
    channel = cfg["discord"]["channel_id"]
    if not payload["product"].get("image_url"):  # no product photo at all: text-only post
        db.set_status(conn, draft_id, "ready")
        discord.reply(channel, row["discord_message_id"],
                      f"ℹ️ Draft #{draft_id} has no product photo, so it will be posted as text only.")
        return
    version = payload.get("image_versions", 0) + 1
    old_ai_image = payload.get("image_path") if payload.get("image_source") == "gemini" else None
    if ai and not feedback and version == 1 and old_ai_image and Path(old_ai_image).exists():
        # Drafted under the old flow, which already paid for an AI image: show that one first.
        payload["image_versions"] = version
        use_ai, path = False, old_ai_image
    else:
        previous = None
        scene_path = payload.get("scene_path")
        if feedback and use_ai and payload.get("image_source") == "gemini" and scene_path and Path(scene_path).exists():
            previous = Path(scene_path).read_bytes()
        path = build_image(payload, DATA / "images" / f"draft-{draft_id}-v{version}.jpg", cfg, ai=use_ai,
                           feedback=feedback if use_ai else "", previous=previous)
        if feedback and use_ai:
            notes.insert(0, f"✏️ {'Edited' if previous else 'New image'} with your note: “{feedback[:300]}”")
    if path is None:  # photo download failed: nothing was paid for; stay put and retry on the next check
        raise RuntimeError("product photo download failed; will retry")
    payload["image_versions"] = version
    if use_ai:
        ok = payload.get("image_source") == "gemini"
        db.log_image_call(conn, draft_id, ok)
        if not ok:
            notes.append("The AI image failed, so this is the plain background.")
    tries = ai_tries_left(cfg, conn, draft_id)
    source = payload["image_source"]
    card = discord.build_image_card(draft_id, payload["product"], source, tries, "\n".join(notes),
                                    reply_to=row["discord_message_id"])
    payload["image_message_id"] = discord.post_image_card(channel, card, path, tries, source)
    db.set_payload(conn, draft_id, payload)
    db.set_status(conn, draft_id, "image_pending")
    print(f"draft #{draft_id}: image card v{version} ({source})")


def cmd_images(cfg: dict, conn) -> None:
    """Caption-approved drafts get their post image now."""
    for row in db.drafts(conn, "approved"):
        try:
            send_image_card(cfg, conn, row, json.loads(row["payload"]), ai=True)
        except Exception as err:  # one bad image must not block the others; retried on the next check
            print(f"! image for draft #{row['id']}: {err}", file=sys.stderr)


def cmd_check_images(cfg: dict, conn) -> None:
    from babebasket import discord

    channel = cfg["discord"]["channel_id"]
    owners = cfg["discord"].get("owner_user_ids", [])
    for row in db.drafts(conn, "image_pending"):
        payload = json.loads(row["payload"])
        message_id = payload.get("image_message_id")
        if not message_id:
            continue
        try:
            decision = discord.read_image_decision(channel, message_id, owners)
            notes = discord.read_image_feedback(channel, message_id, owners, after=payload.get("feedback_seen"))
            if notes["last_id"]:
                payload["feedback_seen"] = notes["last_id"]
                db.set_payload(conn, row["id"], payload)
            if notes["unreadable"] and decision != "reject":
                discord.reply(channel, message_id,
                              "⚠️ I can't read your reply: turn on **Message Content Intent** for the bot "
                              "(Discord Developer Portal → Bot → Privileged Gateway Intents), then reply again.")
            feedback = notes["text"]

            if decision == "reject":
                db.set_status(conn, row["id"], "rejected", decided_at=db.now())
                discord.reply(channel, message_id, f"❌ Draft #{row['id']} dropped.")
            elif feedback:  # a written fix wins over the taps: make the image again as asked
                if ai_tries_left(cfg, conn, row["id"]) > 0:
                    decision = "fix"
                    send_image_card(cfg, conn, row, payload, ai=True, feedback=feedback)
                else:
                    decision = "fix refused"
                    discord.reply(channel, message_id,
                                  "⚠️ No AI image tries left for this post (see [image] in config.toml). "
                                  "Tap ✅, ⬜ or ❌ instead.")
            elif decision == "accept":
                db.set_status(conn, row["id"], "ready", decided_at=db.now())
                discord.reply(channel, message_id, f"✅ Image for draft #{row['id']} approved. Queued for posting.")
            elif decision == "retry":
                send_image_card(cfg, conn, row, payload, ai=True)
            elif decision == "plain":
                send_image_card(cfg, conn, row, payload, ai=False)
            else:
                continue
        except Exception as err:
            print(f"! image decision for draft #{row['id']}: {err}", file=sys.stderr)
            continue
        print(f"draft #{row['id']}: image {decision}")


MAX_PUBLISH_ATTEMPTS = 3


def cmd_publish(cfg: dict, conn) -> None:
    """Post drafts whose caption and image were both approved ('ready'). One failure never blocks the others."""
    from babebasket import discord, threads

    if not cfg.get("threads", {}).get("enabled"):
        return
    channel = cfg["discord"]["channel_id"]
    rows = db.drafts(conn, "ready")
    try:
        if threads.maybe_refresh():
            print("threads token refreshed")
    except Exception as err:  # an old token still works until it expires; say so and carry on
        print(f"! threads token refresh failed: {err}", file=sys.stderr)
    for row in rows:
        payload = json.loads(row["payload"])
        captions = payload["draft"].get("captions", [])
        idx = (row["chosen_caption"] or 1) - 1
        text = captions[idx]["text"] if 0 <= idx < len(captions) else ""
        if not text or len(text) > threads.MAX_CHARS:
            reason = f"caption {idx + 1} is {len(text)} characters; Threads allows {threads.MAX_CHARS}"
            db.set_status(conn, row["id"], "publish_failed")
            discord.reply(channel, row["discord_message_id"], f"⚠️ Draft #{row['id']} not posted: {reason}.")
            print(f"! draft #{row['id']}: {reason}", file=sys.stderr)
            continue
        try:
            image_url = discord.card_image_url(channel, payload.get("image_message_id") or row["discord_message_id"])
            post = threads.publish(text, image_url)
        except Exception as err:
            attempts = payload.get("publish_attempts", 0) + 1
            payload["publish_attempts"] = attempts
            db.set_payload(conn, row["id"], payload)
            final = attempts >= MAX_PUBLISH_ATTEMPTS
            if final:
                db.set_status(conn, row["id"], "publish_failed")
            note = "giving up" if final else f"will retry ({attempts}/{MAX_PUBLISH_ATTEMPTS})"
            try:
                discord.reply(channel, row["discord_message_id"],
                              f"⚠️ Draft #{row['id']} Threads post failed, {note}: {str(err)[:300]}")
            except Exception:
                pass
            print(f"! draft #{row['id']} publish failed ({attempts}): {err}", file=sys.stderr)
            continue
        payload["threads"] = {**post, "published_at": db.now()}
        db.set_payload(conn, row["id"], payload)
        db.set_status(conn, row["id"], "published")
        print(f"draft #{row['id']}: published {post['permalink']}")
        try:
            discord.reply(channel, row["discord_message_id"], f"🧵 Draft #{row['id']} is live on Threads: {post['permalink']}")
        except Exception as err:
            print(f"! reply for draft #{row['id']}: {err}", file=sys.stderr)


def rossmann_product(page_path, ean: str | None) -> dict:
    """A product row from a Rossmann category page saved to disk (no network)."""
    rows = rossmann.parse_page(Path(page_path).read_text(), db.now())
    if not rows:
        raise SystemExit(f"no products found in {page_path}")
    if ean is None:
        return rows[0]
    for row in rows:
        if row["product_id"] == ean:
            return row
    raise SystemExit(f"EAN {ean} not in {page_path}; found: {', '.join(r['product_id'] for r in rows)}")


def cmd_try(cfg: dict, store: str, product_id: str, image: bool = False, page: str | None = None) -> None:
    """One product end to end, printed. Needs OPENROUTER_API_KEY; does not touch Discord or the DB."""
    if store == "rossmann":
        product = rossmann_product(page or "rossmann_probe/category.html", None if product_id == "first" else product_id)
        payload = make_draft(product, cfg)
        print(json.dumps({k: payload[k] for k in ("product", "quote", "draft", "warnings")}, ensure_ascii=False, indent=1))
        if image:
            path = build_image(payload, DATA / "images" / f"try-{product['product_id']}.jpg", cfg)
            print(f"image ({payload.get('image_source', 'none')}): {path}")
        return
    if store != "dm":
        raise SystemExit("try supports dm and rossmann")
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
    sub.add_parser("publish")
    sub.add_parser("daily")
    nt = sub.add_parser("notify", help="post a message to the approval channel")
    nt.add_argument("text")
    t = sub.add_parser("try")
    t.add_argument("store")
    t.add_argument("product_id")
    t.add_argument("--image", action="store_true", help="also render the post image to data/images/")
    t.add_argument("--page", help="rossmann: saved category page (default rossmann_probe/category.html)")
    args = ap.parse_args(argv)

    cfg = config.load()
    if args.cmd == "notify":
        from babebasket import discord

        discord._call("POST", f"/channels/{cfg['discord']['channel_id']}/messages",
                      {"content": args.text.replace("\\n", "\n")[:1900], "allowed_mentions": {"parse": []}})
        return 0
    if args.cmd == "try":
        cmd_try(cfg, args.store, args.product_id, args.image, args.page)
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
        cmd_check(cfg, conn)          # caption cards
        cmd_check_images(cfg, conn)   # image cards
        cmd_images(cfg, conn)         # make images for newly approved captions
        cmd_publish(cfg, conn)
    elif args.cmd == "publish":
        cmd_publish(cfg, conn)
    elif args.cmd == "daily":
        cmd_scrape(cfg, conn)
        cmd_draft(cfg, conn, None)
        cmd_post(cfg, conn)
    return 0
