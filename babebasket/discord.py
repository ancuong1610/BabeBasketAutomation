"""Discord approval cards over the REST API (no always-on bot process needed).

A card is one message with an embed. The bot adds reactions 1️⃣–4️⃣, ✅ and ❌.
The owner taps a number to pick a caption and ✅ to approve (or ❌ to reject).
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
    head = " · ".join(x for x in (labels["title"], labels["hero"]) if x)
    label_text = (f"**{head}**\n" if head else "") + "\n".join(f"▫️ {b}" for b in labels["points"])
    if labels["who_for"]:
        label_text += f"\n👤 {labels['who_for']}"

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


def post_card(channel_id: str, card: dict, n_captions: int, image_path: str | None = None) -> str:
    if image_path:
        message = request_multipart(
            f"{API}/channels/{channel_id}/messages",
            fields={"payload_json": json.dumps(card, ensure_ascii=False)},
            files=[("files[0]", "post.jpg", Path(image_path).read_bytes(), "image/jpeg")],
            headers={"Authorization": f"Bot {secret('DISCORD_BOT_TOKEN')}", "User-Agent": BOT_UA},
            timeout=120,
        )
    else:
        message = _call("POST", f"/channels/{channel_id}/messages", card)
    for e in NUMBERS[:n_captions] + [APPROVE, REJECT]:
        _call("PUT", f"/channels/{channel_id}/messages/{message['id']}/reactions/{_emoji(e)}/@me")
        time.sleep(0.4)  # stay well inside Discord's reaction rate limit
    return message["id"]


def reactors(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    return _call("GET", f"/channels/{channel_id}/messages/{message_id}/reactions/{_emoji(emoji)}?limit=100") or []


def read_decision(channel_id: str, message_id: str, n_captions: int, owners: list[str]) -> dict | None:
    """None while undecided; otherwise {'status': 'approved'|'rejected', 'caption': int|None}.

    One call reads the reaction counts; user lists are only fetched for emojis that
    someone besides the bot has tapped (count > 1), which keeps us clear of rate limits.
    """
    message = _call("GET", f"/channels/{channel_id}/messages/{message_id}")
    tapped = {r["emoji"]["name"] for r in message.get("reactions", []) if r.get("count", 0) > 1}
    owner_set = {str(o) for o in owners}

    def by_owner(emoji: str) -> bool:
        if emoji not in tapped:
            return False
        for user in reactors(channel_id, message_id, emoji):
            if user.get("bot"):
                continue
            if not owner_set or str(user["id"]) in owner_set:
                return True
        return False

    if by_owner(REJECT):
        return {"status": "rejected", "caption": None}
    if not by_owner(APPROVE):
        return None
    picked = [i + 1 for i, e in enumerate(NUMBERS[:n_captions]) if by_owner(e)]
    return {"status": "approved", "caption": picked[0] if picked else 1, "multiple": len(picked) > 1}


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
