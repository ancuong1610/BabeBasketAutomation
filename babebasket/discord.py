"""Discord approval cards over the REST API (no always-on bot process needed).

Two steps per post:
1. Caption card (store packshot, captions): tap 1️⃣–4️⃣ for the caption, ✅ to approve, ❌ to reject.
2. Image card (the AI post image, made only after step 1): ✅ use it, 🔄 make a new one,
   ⬜ use the plain background instead, ❌ drop the post. A text reply to the image card
   ("product bigger, brighter shelf") makes the AI edit that image as asked.
`check` reads the reactions back.
"""

from __future__ import annotations

import time
import urllib.parse

from babebasket.config import secret
import json
from pathlib import Path

from babebasket.http import request_json, request_multipart
from babebasket.llm import image_labels

API = "https://discord.com/api/v10"
NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
APPROVE, REJECT = "✅", "❌"
RETRY, PLAIN = "🔄", "⬜"
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
        {"name": "Sale price", "value": f"{quote['sale_k']}🐠 (margin ~{quote['margin_k']}🐠)", "inline": True},
        {"name": "Rating", "value": f"⭐ {product['rating']:.2f} · {product['review_count']} reviews", "inline": True},
    ]
    if warnings:
        fields.append({"name": "⚠️ Check", "value": "\n".join(warnings)[:1024], "inline": False})

    embed = {
        "title": f"{product['brand']} · {product['name']}"[:256],
        "url": product.get("product_url"),
        "description": description,
        "fields": fields,
        "image": {"url": "attachment://post.jpg"} if has_image
        else ({"url": product["image_url"]} if product.get("image_url") else None),
        "footer": {"text": f"draft {draft_id} · {draft.get('_model', '')}"},
        "color": STORE_COLORS.get(product["store"], 0xF6E7A1),
    }
    card = {
        "content": f"**Draft #{draft_id}** · {badge} · tap a number for the caption, then ✅ to approve or ❌ to reject",
        "embeds": [{k: v for k, v in embed.items() if v}],
        "allowed_mentions": {"parse": []},
    }
    if has_image:
        card["attachments"] = [{"id": 0, "filename": "post.jpg"}]
    return card


def _send(channel_id: str, card: dict, image_path: str | None = None) -> dict:
    if image_path:
        return request_multipart(
            f"{API}/channels/{channel_id}/messages",
            fields={"payload_json": json.dumps(card, ensure_ascii=False)},
            files=[("files[0]", "post.jpg", Path(image_path).read_bytes(), "image/jpeg")],
            headers={"Authorization": f"Bot {secret('DISCORD_BOT_TOKEN')}", "User-Agent": BOT_UA},
            timeout=120,
        )
    return _call("POST", f"/channels/{channel_id}/messages", card)


def _react(channel_id: str, message_id: str, emojis: list[str]) -> None:
    for e in emojis:
        _call("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(e)}/@me")
        time.sleep(0.4)  # stay well inside Discord's reaction rate limit


def post_card(channel_id: str, card: dict, n_captions: int, image_path: str | None = None) -> str:
    message = _send(channel_id, card, image_path)
    _react(channel_id, message["id"], NUMBERS[:n_captions] + [APPROVE, REJECT])
    return message["id"]


def build_image_card(draft_id: int, product: dict, source: str, ai_tries_left: int, note: str = "",
                     reply_to: str | None = None) -> dict:
    kind = "AI scene" if source == "gemini" else "plain background"
    actions = "✅ use this image · " + (f"🔄 new AI image ({ai_tries_left} left) · " if ai_tries_left > 0 else "") \
        + ("⬜ plain background · " if source == "gemini" else "") + "❌ drop the post"
    content = f"🖼️ **Draft #{draft_id}** post image ({kind}): {actions}"
    if ai_tries_left > 0:
        content += "\n💬 Or **reply to this message** with what to fix (e.g. \"sản phẩm to hơn, nền sáng hơn\")."
    if note:
        content += f"\n{note}"
    card = {
        "content": content[:2000],
        "embeds": [{
            "title": f"{product.get('brand', '')} · {product.get('name', '')}"[:256],
            "image": {"url": "attachment://post.jpg"},
            "footer": {"text": f"draft {draft_id} · image"},
            "color": STORE_COLORS.get(product.get("store"), 0xF6E7A1),
        }],
        "attachments": [{"id": 0, "filename": "post.jpg"}],
        "allowed_mentions": {"parse": []},
    }
    if reply_to:
        card["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    return card


def post_image_card(channel_id: str, card: dict, image_path: str, ai_tries_left: int, source: str) -> str:
    message = _send(channel_id, card, image_path)
    emojis = [APPROVE] + ([RETRY] if ai_tries_left > 0 else []) + ([PLAIN] if source == "gemini" else []) + [REJECT]
    _react(channel_id, message["id"], emojis)
    return message["id"]


def reactors(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    return _call("GET", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(emoji)}?limit=100") or []


def owner_taps(channel_id: str, message_id: str, emojis: list[str], owners: list[str]) -> set[str]:
    """Which of `emojis` the owner (or any non-bot user if no owners are set) has tapped.

    One call reads the reaction counts; user lists are only fetched for emojis that
    someone besides the bot has tapped (count > 1), which keeps us clear of rate limits.
    """
    message = _call("GET", f"/channels/{channel_id}/messages/{message_id}")
    tapped = {r["emoji"]["name"] for r in message.get("reactions", []) if r.get("count", 0) > 1}
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
    """Caption card. None while undecided; otherwise {'status': 'approved'|'rejected', 'caption': int|None}."""
    taps = owner_taps(channel_id, message_id, NUMBERS[:n_captions] + [APPROVE, REJECT], owners)
    if REJECT in taps:
        return {"status": "rejected", "caption": None}
    if APPROVE not in taps:
        return None
    picked = [i + 1 for i, e in enumerate(NUMBERS[:n_captions]) if e in taps]
    return {"status": "approved", "caption": picked[0] if picked else 1, "multiple": len(picked) > 1}


def read_image_decision(channel_id: str, message_id: str, owners: list[str]) -> str | None:
    """Image card. None while undecided; else 'reject', 'retry', 'plain' or 'accept' (in that priority)."""
    taps = owner_taps(channel_id, message_id, [APPROVE, RETRY, PLAIN, REJECT], owners)
    for emoji, decision in ((REJECT, "reject"), (RETRY, "retry"), (PLAIN, "plain"), (APPROVE, "accept")):
        if emoji in taps:
            return decision
    return None


def read_image_feedback(channel_id: str, message_id: str, owners: list[str], after: str | None = None) -> dict:
    """Owner's text replies to the image card, newer than message id `after`.

    Returns {"text": joined replies, "last_id": newest reply id or None, "unreadable": bool}.
    `unreadable` means a reply exists but Discord hid its text: the bot needs the
    Message Content Intent (Developer Portal -> Bot -> Privileged Gateway Intents).
    """
    messages = _call("GET", f"/channels/{channel_id}/messages?after={after or message_id}&limit=50") or []
    owner_set = {str(o) for o in owners}
    texts, last_id, unreadable = [], None, False
    for m in sorted(messages, key=lambda m: int(m["id"])):
        if (m.get("message_reference") or {}).get("message_id") != message_id:
            continue
        author = m.get("author") or {}
        if author.get("bot") or (owner_set and str(author.get("id")) not in owner_set):
            continue
        last_id = m["id"]
        content = (m.get("content") or "").strip()
        if content:
            texts.append(content)
        else:
            unreadable = True
    return {"text": " / ".join(texts), "last_id": last_id, "unreadable": unreadable and not texts}


def reply(channel_id: str, message_id: str, text: str) -> None:
    _call(
        "POST",
        f"/channels/{channel_id}/messages",
        {"content": text, "message_reference": {"message_id": message_id}, "allowed_mentions": {"parse": []}},
    )


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
