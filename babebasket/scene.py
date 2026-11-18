"""Ask a Gemini image model (via OpenRouter) to put the packshot in a hand in a drugstore aisle.

With `feedback` and a `previous` scene, the model edits that scene as the owner asked
instead of starting over (the ✏️ edit = reply flow on the Discord image card).

Returns the generated image bytes, or None on any failure: the caller then uses the
plain fallback scene. The model never writes text on the image; postimage.py does that.
"""

from __future__ import annotations

import base64
import sys
from typing import Callable

from babebasket.config import secret
from babebasket.http import request_json
from babebasket.llm import OPENROUTER_URL

PROMPT = """Create a vertical 4:5 photo for an Instagram post of a small German drugstore reseller.

Use the product in the attached image: {product}.
Scene: a young woman's hand (natural nails, no jewellery) holds this exact product up close, \
in the aisle of a German drugstore (dm/Rossmann style), shelves with products softly blurred behind, \
bright natural store light, realistic smartphone photo, shallow depth of field.

Hard rules:
- The product must look exactly like the attached image: same shape, colours, brand, logo and label text. \
Do not redesign, translate or invent anything on the packaging.
- Product sharp, front label facing the camera, fully visible, not cropped.
- Put the product in the CENTRE of the image horizontally. It fills about 45-55% of the image width \
and sits between about 22% and 74% of the image height.
- Keep the top 20% and the bottom 24% of the image calm (soft blurred shelf, no busy details), \
because text will be added there later.
- Do not add any text, captions, stickers, logos, watermarks or price tags."""


EDIT_PROMPT = """Edit the FIRST attached image, a product photo for an Instagram post of a small German \
drugstore reseller. The shop owner asks for these changes:

"{feedback}"

Apply those changes and keep everything else the same.

Hard rules (they win over any request above):
- The product must look exactly like the SECOND attached image (the original packshot): same shape, \
colours, brand, logo and label text. Do not redesign, translate or invent anything on the packaging.
- Product sharp, front label facing the camera, fully visible, centred horizontally.
- Keep the top 20% and the bottom 24% of the image calm, because text will be added there later.
- Do not add any text, captions, stickers, logos, watermarks or price tags.
- Vertical 4:5 photo."""

PAIR_SCENE_NOTE = (
    "\n\nThe attached image shows TWO products: a shampoo and its matching conditioner, sold as a set. "
    "Show BOTH together, held side by side, both front labels fully visible and unchanged; "
    "together they take the central product area described above."
)
FEEDBACK_NOTE = "\n\nThe shop owner also asks (the hard rules above still win): \"{feedback}\""
MAX_FEEDBACK_CHARS = 500


def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image_from_reply(reply: dict) -> bytes | None:
    for choice in reply.get("choices") or []:
        for img in (choice.get("message") or {}).get("images") or []:
            url = (img.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and "," in url:
                return base64.b64decode(url.split(",", 1)[1])
    return None


def generate(
    packshot: bytes,
    product: dict,
    cfg: dict,
    post: Callable[..., dict] = request_json,
    api_key: str | None = None,
    feedback: str = "",
    previous: bytes | None = None,
) -> bytes | None:
    image_cfg = cfg.get("image", {})
    if not image_cfg.get("enabled", True):
        return None

    def data_url(img: bytes) -> str:
        return f"data:{_mime(img)};base64," + base64.b64encode(img).decode()

    feedback = " ".join((feedback or "").split())[:MAX_FEEDBACK_CHARS]
    pair = product.get("pair") or {}
    product_name = f"{product.get('brand', '')} {product.get('name', '')}".strip()
    if pair:
        product_name += f" + {pair.get('name', '')}"
    if feedback and previous:
        content = [
            {"type": "text", "text": EDIT_PROMPT.format(feedback=feedback)},
            {"type": "image_url", "image_url": {"url": data_url(previous)}},
            {"type": "image_url", "image_url": {"url": data_url(packshot)}},
        ]
    else:
        text = PROMPT.format(product=product_name) + (PAIR_SCENE_NOTE if pair else "") \
            + (FEEDBACK_NOTE.format(feedback=feedback) if feedback else "")
        content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_url(packshot)}},
        ]
    body = {
        "model": image_cfg.get("model", "google/gemini-3.1-flash-image"),
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": content}],
    }
    try:
        reply = post(
            OPENROUTER_URL,
            method="POST",
            headers={"Authorization": f"Bearer {api_key or secret('OPENROUTER_API_KEY')}", "X-Title": "babe basket"},
            body=body,
            timeout=180,
            retries=1,
        )
    except Exception as err:  # network, HTTP, JSON: all mean "use the fallback"
        print(f"scene: request failed: {err}", file=sys.stderr)
        return None
    image = _image_from_reply(reply or {})
    if image is None:
        text = ((reply or {}).get("choices") or [{}])[0].get("message", {}).get("content", "")
        print(f"scene: no image in reply: {str(text)[:160]}", file=sys.stderr)
    return image
