"""Discord approval cards over the REST API (no always-on bot process needed).

Two steps per post:
1. Caption card (store packshot, captions): pick a caption and image style, then ✅ to approve or ❌ to reject.
2. Image card (the AI post image, made only after step 1): ✅ accept, ❌ reject,
   ✏️ edit = reply to the card with what to change ("product bigger, brighter shelf"),
   and the AI edits that image as asked.
`check` reads the reactions back.
"""

from __future__ import annotations

import time
import urllib.parse

from babebasket.config import secret
import json
import re
from pathlib import Path

from babebasket.http import request_json, request_multipart
from babebasket.llm import image_labels
from babebasket.pricing import vnd

API = "https://discord.com/api/v10"
NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
PICKER_NUMBERS = NUMBERS + ["7️⃣", "8️⃣", "9️⃣"]
PICKER_ZERO = "0️⃣"
APPROVE, REJECT = "✅", "❌"
EDIT = "✏️"
TEXT = "🔤"  # change only the words on the image: free, no AI call
PHOTO = "📷"
LEGACY_PHOTO = "🖼️"
STYLE_EMOJI = {"🏪": "k0", "🎨": "k1", "🤲": "k3", "🫧": "k4"}
STYLE_LABELS = {"k0": "🏪 trong cửa hàng", "k1": "🎨 flat lay", "k3": "🤲 cận tay", "k4": "🫧 kết cấu"}
TEXT_STYLE_NOTICE = "🔤 Kiểu ảnh này không có chữ, nên bấm 🔤 sẽ không đổi ảnh."
# Owner-only marker: which store the product comes from (never part of the post itself)
STORE_BADGES = {"dm": "🟦 DM", "rossmann": "🟥 ROSSMANN"}
STORE_COLORS = {"dm": 0x1F5FAD, "rossmann": 0xC8102E}
BOT_UA = "DiscordBot (https://localhost, 0.1)"  # format Discord requires


def _call(method: str, path: str, body: dict | None = None):
    return request_json(
        API + path,
        method=method,
        headers={"Authorization": f"Bot {secret('DISCORD_BOT_TOKEN')}", "User-Agent": BOT_UA},
        body=body,
    )


def _emoji(e: str) -> str:
    return urllib.parse.quote(e)


def rating_text(product: dict) -> str:
    if not product.get("review_count") or product.get("rating") is None:
        brand = f" · hãng trung bình {product['brand_rating']:.2f}★" if product.get("brand_rating") else ""
        return f"🆕 chưa có đánh giá (hàng mới){brand}"
    return f"⭐ {product['rating']:.2f} · {product['review_count']} reviews"


GALLERY_ON_CARD = 4  # Discord shows up to 4 photos of embeds that share one link as a grid


def gallery_embeds(product: dict, skip_first: bool = True) -> list[dict]:
    """Extra photos of the product page for the card's grid (the first is already the main image)."""
    url = product.get("product_url")
    photos = (product.get("gallery") or [])[1 if skip_first else 0:]
    if not url:
        return []
    return [{"url": url, "image": {"url": p}} for p in photos[:GALLERY_ON_CARD - 1]]


def price_note(product: dict, quote: dict) -> str:
    """Is the price on the card a discounted one? Say which discount is already counted."""
    coupon = product.get("coupon") or {}
    if coupon.get("in_price"):
        return f"(🏷️ {STORE_BADGES.get(product['store'], '')} đang giảm giá −{coupon.get('percent', 0):g}%, đã có trong giá kệ)"
    if quote.get("coupon_pct"):
        return f"(🎟️ đã trừ coupon −{quote['coupon_pct']:g}%; hết coupon thì giá cao hơn)"
    return "(giá thường, không coupon)"


def build_card(draft_id: int, product: dict, quote: dict, draft: dict, warnings: list[str],
               has_image: bool = False) -> dict:
    labels = image_labels(draft)
    label_text = (f"**{labels['title']}**\n" if labels["title"] else "") + "\n".join(f"▫️ {b}" for b in labels["points"])
    if labels["subtitle"]:
        label_text += f"\n👤 {labels['subtitle']}"

    captions = draft.get("captions", [])
    caption_text = "\n\n".join(
        f"{NUMBERS[i]} **{c.get('style', '')}**\n{c.get('text', '')}" for i, c in enumerate(captions[: len(NUMBERS)])
    )
    description = (
        f"**Reviews:** {draft.get('review_summary', '')}\n"
        f"**Fit for VN:** {draft.get('fit_for_vietnam', '')}\n\n"
        f"**Image labels**\n{label_text}\n\n"
        f"**Captions**\n{caption_text}"
    )[:4096]

    coupon = f" − {quote['coupon_pct']:g}% coupon" if quote["coupon_pct"] else ""
    badge = STORE_BADGES.get(product["store"], product["store"].upper())
    fields = [
        {"name": "Nguồn", "value": badge, "inline": True},
        {"name": "Shelf price", "value": f"€{quote['shelf_eur']:.2f}{coupon} → cost €{quote['cost_eur']:.2f}", "inline": True},
        {"name": "Rating", "value": rating_text(product), "inline": True},
    ]
    if quote.get("sale_vnd"):
        fields.append({"name": "💰 Giá bán dự tính", "value": f"**{vnd(quote['sale_vnd'])}** {price_note(product, quote)}",
                       "inline": False})
        weight = f"~{quote['weight_g']} g (ước lượng)" if quote.get("weight_estimated") else f"{quote['weight_g']} g"
        fields.append({"name": "Vốn · CTV · Cọc · Cân", "value": f"vốn {vnd(quote['cost_vnd'])} · CTV {vnd(quote['ctv_vnd'])}"
                       f" · cọc {vnd(quote['deposit_vnd'])} · {weight}", "inline": False})
    coupon = product.get("coupon")
    if coupon:
        until = f" · đến {coupon['valid_until'][8:10]}.{coupon['valid_until'][5:7]}" if coupon.get("valid_until") else ""
        fields.append({"name": "🎟️ Coupon", "value": f"{coupon.get('label') or ''} (−{coupon['percent']:g}%){until}",
                       "inline": True})
    pair = product.get("pair")
    if pair:
        fields[1]["name"] = "Shelf price (gội + xả)"
        fields[2]["name"] = "Rating gội"
    if warnings:
        fields.append({"name": "⚠️ Check", "value": "\n".join(warnings)[:1024], "inline": False})

    embed = {
        "title": f"{'🧴 Gội · ' if pair else ''}{product['brand']} · {product['name']}"[:256],
        "url": product.get("product_url"),
        "description": description,
        "fields": fields,
        "image": {"url": "attachment://post.jpg"} if has_image
        else ({"url": product["image_url"]} if product.get("image_url") else None),
        "footer": {"text": f"draft {draft_id} · {draft.get('_model', '')}"},
        "color": STORE_COLORS.get(product["store"], 0xF6E7A1),
    }
    embeds = [{k: v for k, v in embed.items() if v}] + gallery_embeds(product, skip_first=not has_image)
    if pair:  # the twin gets its own embed: own link and own photo, so both can be checked before ✅
        twin = {
            "title": f"🧴 Xả · {pair.get('brand') or product['brand']} · {pair.get('name', '')}"[:256],
            "url": pair.get("product_url"),
            "fields": [{"name": "Rating xả", "value": rating_text(pair), "inline": True},
                       {"name": "Giá xả", "value": f"€{pair['price_eur']:.2f}" if pair.get("price_eur") is not None
                        else "?", "inline": True}],
            "image": {"url": pair["image_url"]} if pair.get("image_url") else None,
            "color": STORE_COLORS.get(product["store"], 0xF6E7A1),
        }
        embeds.append({k: v for k, v in twin.items() if v})
        embeds += gallery_embeds(pair)
    card = {
        "content": f"**Draft #{draft_id}** · {badge} · tap a number for the caption, then ✅ to approve or ❌ to reject · "
                   "📷 đổi ảnh sản phẩm gốc: bấm 📷 · bot phản hồi trong khoảng 1 phút"
                   "\n· Kiểu ảnh: 🏪 trong cửa hàng (mặc định, có chữ) · 🎨 flat lay · 🤲 cận tay · 🫧 kết cấu "
                   "(3 kiểu sau: ảnh không chữ)",
        "embeds": embeds,
        "allowed_mentions": {"parse": []},
    }
    if has_image:
        card["attachments"] = [{"id": 0, "filename": "post.jpg"}]
    return card


def _upload(path: str, card: dict, files: list[tuple[str, str, bytes, str]], method: str = "POST") -> dict:
    return request_multipart(
        API + path,
        fields={"payload_json": json.dumps(card, ensure_ascii=False)},
        files=files,
        headers={"Authorization": f"Bot {secret('DISCORD_BOT_TOKEN')}", "User-Agent": BOT_UA},
        timeout=120,
        method=method,
    )


def _send(channel_id: str, card: dict, image_path: str | None = None, extra: list[bytes] | None = None,
          *, message_id: str | None = None) -> dict:
    path = f"/channels/{channel_id}/messages"
    if image_path:
        files = [("files[0]", "post.jpg", Path(image_path).read_bytes(), "image/jpeg")]
        files += [(f"files[{i}]", f"support-{i}.jpg", data, "image/jpeg") for i, data in enumerate(extra or [], 1)]
        if message_id:
            return _upload(f"{path}/{message_id}", card, files, method="PATCH")
        return _upload(path, card, files)
    return _call("POST", path, card)


def _react(channel_id: str, message_id: str, emojis: list[str]) -> None:
    for e in emojis:
        _call("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(e)}/@me")
        time.sleep(0.4)  # stay well inside Discord's reaction rate limit


def post_card(channel_id: str, card: dict, n_captions: int, image_path: str | None = None) -> str:
    message = _send(channel_id, card, image_path)
    _react(channel_id, message["id"], NUMBERS[:n_captions] + [APPROVE, REJECT, *STYLE_EMOJI, PHOTO])
    return message["id"]


def build_image_card(draft_id: int, product: dict, source: str, edits_left: int, note: str = "",
                     reply_to: str | None = None, supporting: list[str] | None = None, has_text: bool = True,
                     attached: int = 0, picker: bool = False) -> dict:
    """`attached` = how many supporting photos are uploaded with the card (support-1.jpg …): those are shown and
    later posted from the card itself, so what the owner approves is exactly what gets posted."""
    kind = "" if source == "gemini" else " (plain background)"
    if edits_left > 0:
        edit = f"✏️ edit: **reply** with what to change ({edits_left} left)"
    else:
        edit = "✏️ no edits left"
    content = f"📷 **Draft #{draft_id}** post image{kind}: ✅ accept · ❌ reject · {edit}"
    if has_text:
        content += " · 🔤 chỉ đổi chữ (miễn phí): bấm 🔤 rồi **reply** chữ mới, dòng 1 = tiêu đề, các dòng sau = ý"
    if note:
        content += f"\n{note}"
    supporting = (supporting or [])[:9]
    attached = min(attached, 9)
    total = attached or len(supporting)
    extra_embeds = []
    hero = "AI" if source == "gemini" else "ảnh chính"
    if total or picker:
        content += f"\n📎 Ảnh 1: {hero}"
    if total:
        photo_range = f"2–{total + 1}" if total > 1 else "2"
        extras = "ảnh phụ" if any(url.startswith("file://") for url in supporting) else "ảnh shop"
        content += f" · ảnh {photo_range}: {extras}"
        urls = [f"attachment://support-{i}.jpg" for i in range(1, attached + 1)] if attached else supporting
        for i, url in enumerate(urls, start=1):
            embed = {"image": {"url": url}}
            if i < GALLERY_ON_CARD and product.get("product_url"):
                embed["url"] = product["product_url"]
            extra_embeds.append(embed)
    if picker:
        content += " (đổi ở bảng ảnh bên dưới)" if total else " · chọn ảnh đăng kèm ở bảng ảnh bên dưới"
    card = {
        "content": content[:2000],
        "embeds": [{k: v for k, v in {
            "title": (f"{product.get('brand', '')} · {product.get('name', '')}"
                      + (f" + {product['pair'].get('name', '')}" if product.get("pair") else ""))[:256],
            "url": product.get("product_url") if total else None,  # same link groups the first four photos as a grid
            "image": {"url": "attachment://post.jpg"},
            "footer": {"text": f"draft {draft_id} · image"},
            "color": STORE_COLORS.get(product.get("store"), 0xF6E7A1),
        }.items() if v}] + extra_embeds,
        "attachments": [{"id": 0, "filename": "post.jpg"}]
                       + [{"id": i, "filename": f"support-{i}.jpg"} for i in range(1, attached + 1)],
        "allowed_mentions": {"parse": []},
    }
    if reply_to:
        card["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    return card


def post_image_card(channel_id: str, card: dict, image_path: str, edits_left: int, extra: list[bytes] | None = None) -> str:
    message = _send(channel_id, card, image_path, extra)
    _react(channel_id, message["id"], [APPROVE, REJECT] + ([EDIT] if edits_left > 0 else []) + [TEXT])
    return message["id"]


def edit_image_card(channel_id: str, message_id: str, card: dict, image_path: str, extra: list[bytes]) -> None:
    _send(channel_id, card, image_path, extra, message_id=message_id)


def write_photo_picker(channel_id: str, image_id: str, draft_id: int, selected: list[int], sheet: bytes,
                       *, message_id: str | None = None, automatic: bool = False) -> str:
    selection = ", ".join(map(str, sorted(selected))) or "chỉ ảnh chính"
    if automatic:
        selection += " (bot tự chọn)"
    card = {
        "content": f"📎 Chọn ảnh đăng kèm cho draft #{draft_id}: bấm số để chọn ảnh (bỏ bấm để bỏ chọn) "
                   f"· 0️⃣ = chỉ đăng ảnh AI · 📷 = thêm ảnh của bạn. Đang chọn: {selection}. "
                   "Bot cập nhật trong khoảng 1 phút.",
        "attachments": [{"id": 0, "filename": "bang-anh.jpg"}],
        "embeds": [{"image": {"url": "attachment://bang-anh.jpg"}}],
        "allowed_mentions": {"parse": []},
    }
    path = f"/channels/{channel_id}/messages"
    if not message_id:
        card["message_reference"] = {"message_id": image_id, "fail_if_not_exists": False}
        card.update(nonce=f"pick-{image_id}", enforce_nonce=True)
    files = [("files[0]", "bang-anh.jpg", sheet, "image/jpeg")]
    result = _upload(f"{path}/{message_id}", card, files, method="PATCH") if message_id else _upload(path, card, files)
    return result["id"]


def reactors(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    return _call("GET", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(emoji)}?limit=100") or []


def owner_taps(channel_id: str, message_id: str, emojis: list[str], owners: list[str]) -> set[str]:
    """Which of `emojis` the owner (or any non-bot user if no owners are set) has tapped.

    One call reads the reaction counts; user lists are only fetched for emojis that
    someone besides the bot has tapped, including emojis the owner added by hand.
    """
    message = _call("GET", f"/channels/{channel_id}/messages/{message_id}")
    tapped = {r["emoji"]["name"] for r in message.get("reactions", [])
              if r.get("count", 0) > int(r.get("me", False))}
    owner_set = {str(o) for o in owners}
    found = set()
    for emoji in emojis:
        if emoji not in tapped:
            continue
        for user in reactors(channel_id, message_id, emoji):
            if not user.get("bot") and (not owner_set or str(user["id"]) in owner_set):
                found.add(emoji)
                break
    return found


def read_decision(channel_id: str, message_id: str, n_captions: int, owners: list[str]) -> dict | None:
    """Read approval, caption, style and photo change taps on a caption card."""
    taps = owner_taps(channel_id, message_id,
                      NUMBERS[:n_captions] + [APPROVE, REJECT, *STYLE_EMOJI, PHOTO, LEGACY_PHOTO], owners)
    photo = bool(taps & {PHOTO, LEGACY_PHOTO})
    if REJECT not in taps and APPROVE not in taps:
        return {"status": "pending", "photo": True} if photo else None
    styles = [style for emoji, style in STYLE_EMOJI.items() if emoji in taps]
    style = {"style": styles[0] if styles else None, "multiple_styles": len(styles) > 1, "photo": photo}
    if REJECT in taps:
        return {"status": "rejected", "caption": None, **style}
    picked = [i + 1 for i, e in enumerate(NUMBERS[:n_captions]) if e in taps]
    return {"status": "approved", "caption": picked[0] if picked else 1, "multiple": len(picked) > 1, **style}


def image_taps(channel_id: str, message_id: str, owners: list[str]) -> set[str]:
    return owner_taps(channel_id, message_id, [APPROVE, REJECT, EDIT, TEXT], owners)


def image_decision(taps: set[str]) -> str | None:
    """None while undecided; else 'reject', 'accept', 'edit' or 'text' (in that priority).

    'edit' (✏️) and 'text' (🔤) only remind the owner to reply; the change itself comes from the reply text.
    """
    for emoji, decision in ((REJECT, "reject"), (APPROVE, "accept"), (EDIT, "edit"), (TEXT, "text")):
        if emoji in taps:
            return decision
    return None


def read_image_decision(channel_id: str, message_id: str, owners: list[str]) -> str | None:
    return image_decision(image_taps(channel_id, message_id, owners))


def replies_after(channel_id: str, after: str) -> list[dict]:
    """Messages in the channel newer than id `after`, oldest first (one call, at most 100)."""
    messages = _call("GET", f"/channels/{channel_id}/messages?after={after}&limit=100") or []
    return sorted(messages, key=lambda m: int(m["id"]))


def read_image_feedback(channel_id: str, message_id: str, owners: list[str], after: str | None = None,
                        accept_plain: bool = False, also: tuple = (), ignore: set[str] | None = None) -> dict:
    """Owner's text replies to the image card, newer than message id `after`.

    With `accept_plain` (only one image card is waiting), a plain message typed into the
    channel after the card also counts, so the owner does not have to use Discord's reply.

    `also`: other message ids a reply may point to (the post's caption card: replying to the
    wrong card of the same post is an easy slip).
    `ignore`: replies already handled as photo uploads.

    Returns the joined text, the individual messages (with attachments), the newest reply id or None,
    and an unreadable flag.
    `unreadable` means a reply exists but Discord hid its text: the bot needs the
    Message Content Intent (Developer Portal -> Bot -> Privileged Gateway Intents).
    """
    messages = _call("GET", f"/channels/{channel_id}/messages?after={after or message_id}&limit=50") or []
    owner_set = {str(o) for o in owners}
    texts, replies, last_id, unreadable = [], [], None, False
    for m in sorted(messages, key=lambda m: int(m["id"])):
        ref = (m.get("message_reference") or {}).get("message_id")
        if ref != message_id and ref not in also and not (accept_plain and ref is None):
            continue
        author = m.get("author") or {}
        if author.get("bot") or (owner_set and str(author.get("id")) not in owner_set):
            continue
        last_id = m["id"]
        if ignore and last_id in ignore:
            continue
        content = (m.get("content") or "").strip()
        if content:
            texts.append(content)
            replies.append(m)
        else:
            unreadable = True
    return {"text": " / ".join(texts), "messages": replies, "last_id": last_id, "unreadable": unreadable and not texts}


WAIT = "⏳"


def unreact(channel_id: str, message_id: str, emoji: str) -> None:
    """Remove the bot's own reaction (e.g. ⏳ once the image card is there)."""
    _call("DELETE", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(emoji)}/@me")


def reply(channel_id: str, message_id: str, text: str) -> str:
    message = _call(
        "POST",
        f"/channels/{channel_id}/messages",
        {"content": text, "message_reference": {"message_id": message_id}, "allowed_mentions": {"parse": []}},
    )
    return message["id"]


def card_image_urls(channel_id: str, message_id: str) -> list[str]:
    """Fresh CDN links of all photos uploaded with the card, post.jpg first, then support-1.jpg, support-2.jpg …"""
    message = _call("GET", f"/channels/{channel_id}/messages/{message_id}") or {}
    # Files shown in an embed (attachment://…) are listed under the embeds, not under attachments.
    found = {}
    for att in message.get("attachments") or []:
        if att.get("url"):
            found.setdefault(att.get("filename") or att["url"].split("?")[0].rsplit("/", 1)[-1], att["url"])
    for embed in message.get("embeds") or []:
        url = (embed.get("image") or {}).get("url") or ""
        name = url.split("?")[0].rsplit("/", 1)[-1]
        if re.fullmatch(r"post\.jpg|support-\d+\.jpg", name):
            found.setdefault(name, url)

    def order(name):
        m = re.search(r"support-(\d+)", name)
        return (0, 0) if name.startswith("post") else (1, int(m.group(1)) if m else 99)
    return [found[n] for n in sorted(found, key=order)]


def card_image_url(channel_id: str, message_id: str) -> str | None:
    """Fresh public CDN link of the card's post image (Discord links are signed and expire, so fetch at use time)."""
    message = _call("GET", f"/channels/{channel_id}/messages/{message_id}") or {}
    for att in message.get("attachments") or []:
        if att.get("url"):
            return att["url"]
    for embed in message.get("embeds") or []:
        url = (embed.get("image") or {}).get("url")
        if url:
            return url
    return None
