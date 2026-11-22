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
import re
import sys
import time
from pathlib import Path

from babebasket import config, db, llm, pricing, shortlist, usage
from babebasket.config import DATA
from babebasket.stores import dm, rossmann


def dm_categories(cfg: dict) -> list[str]:
    """dm categories to scrape: every group's list (plus a legacy [dm] categories list), no repeats."""
    names = [c for g in cfg.get("groups", {}).values() for c in g.get("dm", [])]
    names += cfg.get("dm", {}).get("categories", [])
    return list(dict.fromkeys(names))


def cmd_scrape(cfg: dict, conn, stores: tuple[str, ...] = ("dm", "rossmann")) -> None:
    all_rows = []
    for category in dm_categories(cfg) if "dm" in stores else []:
        rows = dm.crawl_category(category)
        print(f"dm  {category}: {len(rows)} products")
        if not rows:
            print(f"    ! no products: is '{category}' an exact dm category name?", file=sys.stderr)
        all_rows += rows
        time.sleep(dm.DELAY_SECONDS)
    offer_rows = []
    for url in cfg.get("rossmann", {}).get("offer_pages", []) if "rossmann" in stores else []:
        try:  # weekly flyer / online offers: discounts become ranking boosts (coupons table)
            found = rossmann.crawl_category(url)
        except Exception as err:
            print(f"rossmann offers {url}: failed: {err}", file=sys.stderr)
            continue
        print(f"rossmann offers {url}: {len(found)} products, {sum(1 for r in found if r.get('offer_pct'))} discounted")
        offer_rows += found
        all_rows += found
    if offer_rows:
        from babebasket import coupons
        print(f"rossmann offers: {coupons.replace_store_offers(conn, offer_rows)} discounts saved")
    for url in cfg.get("rossmann", {}).get("categories", []) if "rossmann" in stores else []:
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
        print(f"{p['score']:6.2f}  {p['group']:5} {p['store']:8} {p['brand'][:20]:20} €{p['price_eur']:<6}  "
              + (f"NEW (brand {p.get('brand_rating', 0):.2f}★)" if p.get("is_new") else f"{p['rating']:.2f}★ {p['review_count']:>5}")
              + f"  {p['name'][:50]}")
    return picks


def enrich(product: dict) -> tuple[dict, list[dict]]:
    if product["store"] == "dm":
        detail = dm.fetch_detail(product["product_id"])
        time.sleep(1)
        reviews = dm.fetch_reviews(product["product_id"])
        if detail.get("images"):
            product["image_url"] = detail["images"][0]
        if detail.get("gallery"):
            product["gallery"] = clean_gallery(detail["gallery"])
        return detail, reviews
    return {"claims": [], "breadcrumbs": [], "description": ""}, []


MAX_GALLERY = 8
SAME_PHOTO_BITS = 5  # dHash distance at or below which two photos count as the same


def clean_gallery(items: list[dict], fetch=None) -> list[str]:
    """The product page's photos without repeats: same link, near-identical picture, or photos dm marks as AI-made.

    Only the small thumbnails are downloaded for the comparison. Order is kept (first = store packshot).
    """
    from babebasket import postimage

    fetch = fetch or fetch_bytes
    kept, hashes, seen = [], [], set()
    for item in items:
        url = item.get("url")
        if not url or url in seen or item.get("ai"):
            continue
        seen.add(url)
        try:
            h = postimage.dhash(fetch(item.get("thumb") or url))
        except Exception:  # thumbnail not readable: keep the photo, it just is not compared
            h = None
        if h is not None and any(bin(h ^ o).count("1") <= SAME_PHOTO_BITS for o in hashes):
            continue
        if h is not None:
            hashes.append(h)
        kept.append(url)
        if len(kept) >= MAX_GALLERY:
            break
    return kept


def make_draft(product: dict, cfg: dict) -> dict:
    detail, reviews = enrich(product)
    pair, pair_detail, price = product.get("pair"), None, product["price_eur"]
    if pair:  # shampoo + matching conditioner, sold together
        time.sleep(1)
        pair_detail, pair_reviews = enrich(pair)
        reviews = reviews[:8] + pair_reviews[:6]
        price = round(price + (pair.get("price_eur") or 0), 2)
    coupon = product.get("coupon") or {}
    quote = pricing.quote(price, product["store"], cfg, None if coupon.get("in_price") else coupon.get("percent"))
    draft = llm.draft_post(product, detail, reviews, cfg, pair=pair, pair_detail=pair_detail)
    warnings = llm.warnings(draft, had_reviews=bool(reviews))
    return {"product": product, "detail": detail, "quote": quote, "draft": draft, "warnings": warnings}


def fetch_bytes(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (babebasket)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def supporting_photos(product: dict, cfg: dict) -> list[str]:
    """Shop photos (swatches, shades) to post after the AI image. Never blocks the image itself."""
    from babebasket import scene

    try:
        return scene.select_supporting(product, cfg, fetch_bytes)
    except Exception as err:
        print(f"! supporting photos for {product.get('product_id')}: {err}", file=sys.stderr)
        return []


def labels_for(payload: dict) -> dict:
    """The words on the post image: the draft's labels, or the owner's own text sent with 🔤."""
    labels = llm.image_labels(payload["draft"])
    return {**labels, **payload.get("labels_override", {})}


def labels_from_reply(text: str, current: dict) -> dict:
    """🔤 reply: first line is the title, the next lines (up to 3) the points; one line changes only the title."""
    lines = [re.sub(r"^\s*[•▫️\-–—*·]+\s*", "", l).strip() for l in re.split(r"\n| / ", text)]
    lines = [l for l in lines if l]
    if not lines:
        return {}
    out = {"title": lines[0][:80]}
    if len(lines) > 1:
        out["points"] = [l[:80] for l in lines[1:4]]
    return out


def reletter(payload: dict, out, fetch_bytes=None) -> str | None:
    """Put the (new) words on the image's existing scene again. Free: no AI call."""
    import io

    from PIL import Image

    from babebasket import postimage

    scene_path = payload.get("scene_path")
    if scene_path and Path(scene_path).exists():
        base = Image.open(scene_path)
    else:  # plain-background image: rebuild it from the packshot
        fetch = fetch_bytes or globals()["fetch_bytes"]
        product = payload["product"]
        packshot = fetch(product["image_url"])
        if (product.get("pair") or {}).get("image_url"):
            packshot = postimage.side_by_side(packshot, fetch(product["pair"]["image_url"]))
        base = postimage.fallback_scene(packshot)
    path = postimage.save(postimage.render(base, labels_for(payload)), out)
    payload["image_path"] = str(path)
    return str(path)


def build_image(payload: dict, out, cfg: dict, fetch_bytes=fetch_bytes, gen=None, ai: bool = True,
                feedback: str = "", previous: bytes | None = None, describe=None, check=None) -> str | None:
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

    if gen is None:  # the real image model: also read the packaging first and check the result after
        gen, describe, check = scene.generate, describe or scene.describe_packaging, check or scene.check_fidelity
    product = payload["product"]
    if not product.get("image_url"):
        return None
    try:
        packshot = fetch_bytes(product["image_url"])
        pair = product.get("pair") or {}
        if pair.get("image_url"):  # shampoo + conditioner: both bottles in one picture
            packshot = postimage.side_by_side(packshot, fetch_bytes(pair["image_url"]))
    except Exception as err:
        print(f"! packshot download failed for {product['product_id']}: {err}", file=sys.stderr)
        return None
    labels = labels_for(payload)
    source, base = "fallback", None
    facts = ""
    if ai and describe:  # e.g. "opaque iridescent pink bottle": stops the model inventing clear plastic
        facts = payload.get("packaging_facts") or describe(packshot, cfg)
        payload["packaging_facts"] = facts
    extra = {k: v for k, v in (("feedback", feedback), ("previous", previous), ("facts", facts)) if v}
    generated = gen(packshot, product, cfg, **extra) if ai else None
    payload.pop("fidelity", None)
    if generated:
        try:
            base = Image.open(io.BytesIO(generated))
            base.load()
            source = "gemini"
            if check:
                payload["fidelity"] = check(packshot, generated, cfg, facts=facts)
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
            usage.flush(conn, None)
            print(f"! {product['brand']} {product['name'][:40]}: {err}", file=sys.stderr)
            continue
        # No image yet: the post image is made only after the caption is approved (see cmd_images).
        draft_id = db.add_draft(conn, product["store"], product["product_id"], payload)
        usage.flush(conn, draft_id)
        tokens = payload["draft"].get("_usage", {})
        print(f"draft #{draft_id}: {product['brand']} · {product['name'][:50]}  "
              f"({tokens.get('total_tokens', '?')} tokens)")


def cmd_post(cfg: dict, conn) -> None:
    from babebasket import discord

    channel = cfg["discord"]["channel_id"]
    if not channel:
        raise SystemExit("Set discord.channel_id in config.toml or DISCORD_CHANNEL_ID in .env")
    failed = []
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
            failed.append(row["id"])
            continue
        db.set_status(conn, row["id"], "pending", discord_message_id=message_id)
        print(f"card for draft #{row['id']} -> message {message_id}")
        time.sleep(1)
    if failed:  # make the run fail (n8n shows it red, run.sh alerts) instead of failing silently
        raise RuntimeError(f"{len(failed)} card(s) not sent (drafts {', '.join(f'#{i}' for i in failed)}); "
                           "they stay drafted: fix the cause (e.g. DISCORD_BOT_TOKEN in .env), then run: post")


NO_SUPPORTING = re.compile(r"(không|ko|k|bỏ)\s*(dùng\s*|kèm\s*)?ảnh\s*phụ", re.I)
MAX_SOURCE_BYTES = 15 * 1024 * 1024
MIN_SOURCE_SIDE = 300
IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)


def source_url(message: dict) -> str | None:
    """The image the owner sent: an attached picture, else the first link in the text."""
    for att in message.get("attachments") or []:
        if (att.get("content_type") or "").startswith("image/") or IMAGE_EXT.search(att.get("filename") or ""):
            return att.get("url")
    link = re.search(r"https?://\S+", message.get("content") or "")
    return link.group(0).rstrip(">)") if link else None


def gallery_pick(conn, draft_id: int, text: str) -> str | None:
    """'ảnh 3' (or 'xả ảnh 2' for a pair's conditioner): that photo from the product page's gallery."""
    pick = re.search(r"ảnh\s*(?:số\s*)?(\d+)", text, re.I)
    if not pick:
        return None
    product = json.loads(conn.execute("SELECT payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0])["product"]
    if product.get("pair") and re.search(r"xả|conditioner|spülung", text, re.I):
        product = product["pair"]
    gallery = product.get("gallery") or []
    n = int(pick.group(1))
    return gallery[n - 1] if 1 <= n <= len(gallery) else None


def replace_source(conn, draft_id: int, url: str, text: str = "", fetch=None) -> str:
    """Swap the product photo the AI image will be made from. Returns which bottle changed ('' for a single product).

    The photo must be a real image of at least 300 px; it is stored under data/sources/ and the
    original link is kept in `source_original`. Product, caption and price stay as they are.
    """
    import io

    from PIL import Image

    data = (fetch or fetch_bytes)(url)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("ảnh lớn hơn 15 MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("không phải file ảnh") from None
    if min(img.size) < MIN_SOURCE_SIDE:
        raise ValueError(f"ảnh quá nhỏ ({img.width}×{img.height}, cần tối thiểu {MIN_SOURCE_SIDE} px)")
    row = conn.execute("SELECT payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    payload = json.loads(row["payload"])
    product = payload["product"]
    to_pair = bool(product.get("pair")) and re.search(r"xả|conditioner|spülung", text, re.I) is not None
    target = product["pair"] if to_pair else product
    out = DATA / "sources" / f"draft-{draft_id}-{'pair' if to_pair else 'main'}-{int(time.time())}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92)
    target.setdefault("source_original", target.get("image_url"))
    target["image_url"] = out.resolve().as_uri()
    target["source_replaced_from"] = url.split("?")[0]
    payload.pop("packaging_facts", None)  # facts described the old photo
    db.set_payload(conn, draft_id, payload)
    return ("dầu xả" if to_pair else "dầu gội") if product.get("pair") else ""


def cmd_source_replies(cfg: dict, conn) -> None:
    """🖼️ The owner replied to a caption card with a photo or a photo link: use it as the product photo.

    Runs before the ✅ is read, so an image is never made from the old photo once a new one was sent.
    One Discord call per run for all waiting cards.
    """
    from babebasket import coupons, discord

    channel = cfg["discord"]["channel_id"]
    owners = {str(o) for o in cfg["discord"].get("owner_user_ids", [])}
    cards = {r["discord_message_id"]: r["id"] for r in db.drafts(conn, "pending") if r["discord_message_id"]}
    if not cards:
        return
    seen = coupons._kv(conn, "source_replies_seen") or "0"
    numeric = [int(m) for m in cards if str(m).isdigit()]
    after = str(max(int(seen), min(numeric) - 1 if numeric else 0))
    for m in discord.replies_after(channel, after):
        after = m["id"]
        card = (m.get("message_reference") or {}).get("message_id")
        author = m.get("author") or {}
        if card not in cards or author.get("bot") or (owners and str(author.get("id")) not in owners):
            continue
        draft_id = cards[card]
        url = source_url(m) or gallery_pick(conn, draft_id, m.get("content") or "")
        if not url:
            continue
        try:
            which = replace_source(conn, draft_id, url, m.get("content") or "")
            discord.reply(channel, card, f"🖼️ Đã thay ảnh gốc{' ' + which if which else ''} cho draft #{draft_id}. "
                                         "Bấm ✅ khi sẵn sàng: ảnh AI sẽ dùng ảnh mới.")
            print(f"draft #{draft_id}: source photo replaced")
        except Exception as err:
            discord.reply(channel, card, f"⚠️ Không dùng được ảnh này: {str(err)[:150]}. "
                                         "Gửi ảnh JPG/PNG khác hoặc link trực tiếp tới ảnh.")
            print(f"! draft #{draft_id}: source photo not usable: {err}", file=sys.stderr)
    coupons._kv(conn, "source_replies_seen", after)


def cmd_check(cfg: dict, conn) -> None:
    from babebasket import discord

    channel = cfg["discord"]["channel_id"]
    owners = cfg["discord"].get("owner_user_ids", [])
    for row in db.drafts(conn, "pending"):
        payload = json.loads(row["payload"])
        n = len(payload["draft"].get("captions", []))
        time.sleep(0.5)  # several waiting cards are read one after another: stay under Discord's rate limit
        try:
            decision = discord.read_decision(channel, row["discord_message_id"], n, owners)
        except Exception as err:  # e.g. a deleted card (404): skip it, keep reading the others
            print(f"! caption card of draft #{row['id']}: {err}", file=sys.stderr)
            continue
        if not decision:
            continue
        db.set_status(conn, row["id"], decision["status"], chosen_caption=decision["caption"], decided_at=db.now())
        if decision["status"] == "approved":
            note = " (several numbers tapped, used the first)" if decision.get("multiple") else ""
            discord.reply(channel, row["discord_message_id"],
                          f"✅ Đã nhận draft #{row['id']}, caption {decision['caption']}{note}. ⏳ Đang tạo ảnh, "
                          "khoảng 1 phút nữa thẻ ảnh sẽ hiện ngay dưới đây.")
            try:
                discord._react(channel, row["discord_message_id"], [discord.WAIT])
            except Exception as err:
                print(f"! ⏳ on draft #{row['id']}: {err}", file=sys.stderr)
        else:
            discord.reply(channel, row["discord_message_id"], f"❌ Draft #{row['id']} rejected.")
        print(f"draft #{row['id']}: {decision['status']} (caption {decision['caption']})")


def run_coupons(cfg: dict, conn) -> None:
    """Read #coupons; a problem there must never stop approvals or the daily run."""
    from babebasket import coupons

    try:
        coupons.process_channel(cfg, conn)
    except Exception as err:
        print(f"! coupons: {err}", file=sys.stderr)
    usage.flush(conn, None)


def ai_tries_left(cfg: dict, conn, draft_id: int) -> int:
    """Paid AI image requests still allowed for this draft, within the daily cap."""
    ic = cfg.get("image", {})
    if not ic.get("enabled", True):
        return 0
    per_post = ic.get("max_per_post", 2) - db.image_calls_for(conn, draft_id)
    per_day = ic.get("max_per_day", 4) - db.image_calls_today(conn)
    return max(0, min(per_post, per_day))


def send_image_card(cfg: dict, conn, row, payload: dict, ai: bool, note: str = "", feedback: str = "",
                    text: str = "") -> None:
    """Make the post image (AI if allowed, else plain) and send it to Discord for approval.

    With `feedback`, the previous AI scene (if any) is edited as asked instead of starting over.
    With `text` (🔤), only the words on the image change: the same scene is lettered again, no AI call.
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
    unposted = payload.get("unposted_image") or {}
    if (unposted.get("path") and Path(unposted["path"]).exists() and unposted.get("feedback", "") == feedback
            and unposted.get("text", "") == text):
        # Made and paid for on an earlier run whose Discord card failed: send that image, do not make a new one.
        path, version, use_ai, notes = unposted["path"], unposted["version"], False, unposted.get("notes", notes)
    elif text:
        payload["labels_override"] = labels_from_reply(text, labels_for(payload))
        path = reletter(payload, DATA / "images" / f"draft-{draft_id}-v{version}.jpg")
        use_ai = False
        notes.insert(0, "🔤 Đã đổi chữ trên ảnh (không dùng AI, không tốn phí).")
    elif ai and not feedback and version == 1 and old_ai_image and Path(old_ai_image).exists():
        # Drafted under the old flow, which already paid for an AI image: show that one first.
        payload["image_versions"] = version
        use_ai, path = False, old_ai_image
    else:
        if (use_ai and not feedback and payload["product"].get("group") == "makeup"
                and not payload["product"].get("pair") and "supporting" not in payload):
            payload["supporting"] = supporting_photos(payload["product"], cfg)
        previous = None
        scene_path = payload.get("scene_path")
        if feedback and use_ai and payload.get("image_source") == "gemini" and scene_path and Path(scene_path).exists():
            previous = Path(scene_path).read_bytes()
        path = build_image(payload, DATA / "images" / f"draft-{draft_id}-v{version}.jpg", cfg, ai=use_ai,
                           feedback=feedback if use_ai else "", previous=previous)
        usage.flush(conn, draft_id)
        if feedback and use_ai:
            notes.insert(0, f"✏️ {'Edited' if previous else 'New image'} with your note: “{feedback[:300]}”")
    if path is None:  # photo download failed: nothing was paid for; stay put and retry on the next check
        raise RuntimeError("product photo download failed; will retry")
    if not unposted.get("path") or unposted.get("path") != path:
        payload["image_versions"] = version
        if use_ai:
            ok = payload.get("image_source") == "gemini"
            db.log_image_call(conn, draft_id, ok)
            if not ok:
                notes.append("The AI image failed, so this is the plain background.")
        fidelity = payload.get("fidelity") or {}
        if fidelity.get("match") is False:
            notes.append("⚠️ Có thể khác bao bì thật: " + "; ".join(fidelity.get("problems", []))
                         + ". Bấm ✏️ để sửa hoặc ❌ để bỏ.")
        # Saved before the Discord card goes out: if posting fails, the next run resends this image.
        payload["unposted_image"] = {"path": path, "version": version, "feedback": feedback, "text": text,
                                     "notes": notes}
        db.set_payload(conn, draft_id, payload)
    edits_left = ai_tries_left(cfg, conn, draft_id)
    source = payload["image_source"]
    card = discord.build_image_card(draft_id, payload["product"], source, edits_left, "\n".join(notes),
                                    reply_to=row["discord_message_id"], supporting=payload.get("supporting"))
    payload["image_message_id"] = discord.post_image_card(channel, card, path, edits_left)
    payload.pop("unposted_image", None)
    try:  # the image card is there: the caption card no longer waits
        discord.unreact(channel, row["discord_message_id"], discord.WAIT)
    except Exception:
        pass
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
    waiting = db.drafts(conn, "image_pending")
    for row in waiting:
        payload = json.loads(row["payload"])
        message_id = payload.get("image_message_id")
        if not message_id:
            continue
        try:
            unposted = payload.get("unposted_image")
            if unposted:  # an edited image was made but its card failed to post: send it now
                send_image_card(cfg, conn, row, payload, ai=True, feedback=unposted.get("feedback", ""))
                continue
            taps = discord.image_taps(channel, message_id, owners)
            decision = discord.image_decision(taps)
            # With a single image card waiting, a plain message (not a Discord reply) is clearly for it.
            notes = discord.read_image_feedback(channel, message_id, owners, after=payload.get("feedback_seen"),
                                                accept_plain=len(waiting) == 1)
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
            elif feedback and payload.get("supporting") and NO_SUPPORTING.search(feedback):
                decision = "no supporting photos"
                payload["supporting"] = []
                db.set_payload(conn, row["id"], payload)
                discord.reply(channel, message_id, "📎 Ok, bài này chỉ đăng ảnh AI, không kèm ảnh của shop.")
            elif feedback and discord.TEXT in taps:  # 🔤 + reply: new words on the same scene, no AI call
                decision = "text"
                send_image_card(cfg, conn, row, payload, ai=False, text=feedback)
            elif feedback:  # a written edit wins over ✅: make the image again as asked
                if ai_tries_left(cfg, conn, row["id"]) > 0:
                    decision = "edit"
                    discord.reply(channel, message_id, "✏️ Đã nhận ghi chú. ⏳ Đang sửa ảnh, khoảng 1 phút nữa "
                                                       "ảnh mới sẽ hiện ngay dưới đây.")
                    send_image_card(cfg, conn, row, payload, ai=True, feedback=feedback)
                else:
                    decision = "edit refused"
                    discord.reply(channel, message_id,
                                  "⚠️ No edits left for this post (see [image] in config.toml). Tap ✅ or ❌.")
            elif decision == "accept":
                db.set_status(conn, row["id"], "ready", decided_at=db.now())
                discord.reply(channel, message_id, f"✅ Image for draft #{row['id']} approved. Queued for posting.")
            elif decision == "edit" and payload.get("edit_hint_for") != message_id:  # ✏️ tapped, no reply yet
                payload["edit_hint_for"] = message_id
                db.set_payload(conn, row["id"], payload)
                discord.reply(channel, message_id,
                              "✏️ Reply to the image card with what to change, e.g. \"sản phẩm to hơn, nền sáng hơn\".")
            elif decision == "text" and payload.get("edit_hint_for") != message_id:  # 🔤 tapped, no reply yet
                payload["edit_hint_for"] = message_id
                db.set_payload(conn, row["id"], payload)
                discord.reply(channel, message_id, "🔤 Reply thẻ ảnh bằng chữ mới: dòng 1 là tiêu đề, các dòng sau "
                                                   "là các ý (tối đa 3). Chỉ 1 dòng thì chỉ đổi tiêu đề.")
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
            post = threads.publish(text, image_url, extra_images=payload.get("supporting") or None)
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
    sc_ = sub.add_parser("scrape")
    sc_.add_argument("--store", choices=["dm", "rossmann"], help="only this store")
    sub.add_parser("shortlist")
    d = sub.add_parser("draft")
    d.add_argument("--limit", type=int)
    sub.add_parser("post")
    sub.add_parser("check")
    sub.add_parser("publish")
    cs = sub.add_parser("costs", help="what OpenRouter calls cost, per stage and draft")
    cs.add_argument("--days", type=int, default=7)
    dl = sub.add_parser("daily")
    dl.add_argument("--store", choices=["dm", "rossmann"], help="scrape only this store")
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
    stores = (args.store,) if getattr(args, "store", None) else ("dm", "rossmann")
    if args.cmd == "scrape":
        cmd_scrape(cfg, conn, stores)
    elif args.cmd == "shortlist":
        cmd_shortlist(cfg, conn)
    elif args.cmd == "draft":
        cmd_draft(cfg, conn, args.limit)
    elif args.cmd == "post":
        cmd_post(cfg, conn)
    elif args.cmd == "check":
        run_coupons(cfg, conn)        # new screenshots/notes in #coupons
        try:
            cmd_source_replies(cfg, conn)  # 🖼️ new product photos sent as replies to caption cards
        except Exception as err:
            print(f"! source photos: {err}", file=sys.stderr)
        cmd_check(cfg, conn)          # caption cards
        cmd_check_images(cfg, conn)   # image cards
        cmd_images(cfg, conn)         # make images for newly approved captions
        cmd_publish(cfg, conn)
    elif args.cmd == "publish":
        cmd_publish(cfg, conn)
    elif args.cmd == "costs":
        print(usage.report(conn, args.days))
    elif args.cmd == "daily":
        run_coupons(cfg, conn)
        cmd_scrape(cfg, conn, stores)
        cmd_draft(cfg, conn, None)
        cmd_post(cfg, conn)
    return 0
