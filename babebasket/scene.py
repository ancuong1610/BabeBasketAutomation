"""Ask a Gemini image model (via OpenRouter) to put the packshot in a hand in a drugstore aisle.

With `feedback` and a `previous` scene, the model edits that scene as the owner asked
instead of starting over (the ✏️ edit = reply flow on the Discord image card).

Returns the generated image bytes, or None on any failure: the caller then uses the
plain fallback scene. The model never writes text on the image; postimage.py does that.
"""

from __future__ import annotations

import base64
import re
import sys
from typing import Callable

from babebasket.config import secret
from babebasket.http import request_json
from babebasket.llm import OPENROUTER_URL

PROMPT = """Create a vertical 4:5 photo for an Instagram post of a small German drugstore reseller.

Use the product in the attached image: {product}.
Scene: the hand of a young East/Southeast Asian woman holds this exact product up close, \
in the aisle of a German drugstore (dm/Rossmann style), shelves with products softly blurred behind, \
bright natural store light, realistic smartphone photo, shallow depth of field.
The hand is small and slim, like a petite Vietnamese woman's: about 16 cm from wrist to fingertip, \
palm about 7 cm wide, natural short nails, no jewellery.{size}

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
    "Show BOTH: two hands of the same woman, each holding one bottle side by side, both front labels "
    "fully visible and unchanged. Keep their true relative sizes (as in the attached image; a 1,000 ml "
    "bottle is far bigger than a 200 ml one). Together they take the central product area described above."
)


def _volume_ml(name: str) -> float | None:
    """'…, 1.000 ml' -> 1000; '…, 1 l' -> 1000; '…, 50 g' -> 50 (grams treated like ml)."""
    m = re.search(r"(\d[\d.,]*)\s*(ml|l|g)\b", name or "", re.I)
    if not m:
        return None
    num = m.group(1)
    num = num.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", num) else num.replace(",", ".")
    try:
        value = float(num)
    except ValueError:
        return None
    return value * 1000 if m.group(2).lower() == "l" else value


# How big a product of that amount looks next to a ~16 cm hand. Heights only for bottles:
# jars and tubes keep the shape of the packshot.
SIZE_GUIDE = [
    (60, "small: it fits inside the hand"),
    (160, "about hand-sized"),
    (320, "medium: a bottle of this size is a bit taller than the hand (about 17-20 cm)"),
    (600, "large: a bottle of this size is clearly taller than the hand (about 21-24 cm); "
          "a jar of this size is wider than the small palm"),
    (10**9, "very big and heavy, around 1 kg: a bottle of this size is about 26-29 cm tall, much taller "
            "than the small hand, held from the side or supported from below, and it looks heavy"),
]


def size_hint(name: str) -> str:
    ml = _volume_ml(name)
    if not ml:
        return ""
    amount = re.search(r"\d[\d.,]*\s*(ml|l|g)\b", name, re.I).group(0)
    desc = next(text for limit, text in SIZE_GUIDE if ml <= limit)
    return (f"\nTrue scale matters: the product holds {amount}, {desc}. "
            "Show it at its real size relative to the small hand; do not shrink it to fit the hand.")


FEEDBACK_NOTE = "\n\nThe shop owner also asks (the hard rules above still win): \"{feedback}\""
MAX_FEEDBACK_CHARS = 500


DESCRIBE_PROMPT = """You see the official photo of a drugstore product (or two products side by side).
An image model must reproduce the packaging EXACTLY in a new photo. Describe what it must get right.
Be concrete about whether the container is opaque or see-through: iridescent, pearlescent or metallic
finishes are usually OPAQUE coatings, not clear plastic.
Return only JSON:
{"products": [{"container": "e.g. pump bottle / squeeze tube / flip-cap bottle / jar",
               "opacity": "opaque or transparent, and what that looks like",
               "body_colour_and_finish": "...", "cap_or_pump": "colour, material, shape",
               "label": "main colours and layout of the front label", "other": "anything distinctive"}]}"""

PACKAGING_NOTE = ("\n\nPackaging facts read from the real product photo. Reproduce them exactly; they "
                  "override any idea of what such a product usually looks like:\n{facts}")

CHECK_PROMPT = """The FIRST image is the official photo of the real product(s). The SECOND is an AI-made photo
that must show the same product(s). Compare only the packaging: container shape, opaque vs transparent,
body colour and finish, cap/pump colour and shape, label layout, brand and product name.
Ignore background, hand, lighting, angle and size. Small blur is fine. Tiny small-print typos are
not worth flagging; wrong brand/product names are.{facts}
Return only JSON: {"match": true or false, "problems": ["short problem in Vietnamese", ...]}
"match" is false only for differences a customer would notice when the real product arrives."""


def _vision_json(prompt: str, images: list[bytes], cfg: dict, post: Callable[..., dict], api_key: str | None,
                 stage: str = "vision") -> dict:
    """Ask the text model (it reads images) a question about images; returns parsed JSON."""
    from babebasket import usage
    from babebasket.llm import parse_json_reply

    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:{_mime(img)};base64," + base64.b64encode(img).decode()}}
        for img in images]
    reply = post(OPENROUTER_URL, method="POST", timeout=90, retries=1,
                 headers={"Authorization": f"Bearer {api_key or secret('OPENROUTER_API_KEY')}", "X-Title": "babe basket"},
                 body={"model": cfg.get("llm", {}).get("model", "google/gemini-3.8-flash"), "temperature": 0,
                       "response_format": {"type": "json_object"}, **usage.REQUEST,
                       "messages": [{"role": "user", "content": content}]})
    usage.record(stage, reply, images_in=len(images))
    return parse_json_reply(reply["choices"][0]["message"]["content"])


def describe_packaging(packshot: bytes, cfg: dict, post: Callable[..., dict] = request_json,
                       api_key: str | None = None) -> str:
    """Concrete packaging facts for the image prompt, e.g. 'opaque iridescent pink bottle'. '' on failure."""
    try:
        data = _vision_json(DESCRIBE_PROMPT, [packshot], cfg, post, api_key, stage="packaging")
    except Exception as err:
        print(f"scene: packaging description failed: {err}", file=sys.stderr)
        return ""
    lines = []
    for i, prod in enumerate(data.get("products") or [], 1):
        if isinstance(prod, dict):
            facts = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in prod.items() if v)
            lines.append(f"- product {i}: {facts}")
    return "\n".join(lines)[:1500]


def check_fidelity(packshot: bytes, generated: bytes, cfg: dict, post: Callable[..., dict] = request_json,
                   api_key: str | None = None, facts: str = "") -> dict:
    """{'match': bool, 'problems': [...]} comparing the AI photo with the real packaging.
    If the check itself fails, returns match True with no problems (the owner still reviews the image)."""
    try:
        known = f"\nFacts about the real packaging (trust these over your reading of the first image):\n{facts}" if facts else ""
        data = _vision_json(CHECK_PROMPT.replace("{facts}", known), [packshot, generated], cfg, post, api_key,
                            stage="fidelity")
    except Exception as err:
        print(f"scene: fidelity check failed: {err}", file=sys.stderr)
        return {"match": True, "problems": [], "checked": False}
    problems = [str(p) for p in (data.get("problems") or []) if p][:4]
    match = data.get("match") is not False
    if not match and not problems:
        problems = ["ảnh AI khác bao bì thật"]
    return {"match": match,
            "problems": problems, "checked": True}


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
    facts: str = "",
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
            {"type": "text", "text": EDIT_PROMPT.format(feedback=feedback)
             + (PACKAGING_NOTE.format(facts=facts) if facts else "")},
            {"type": "image_url", "image_url": {"url": data_url(previous)}},
            {"type": "image_url", "image_url": {"url": data_url(packshot)}},
        ]
    else:
        size = "" if pair else size_hint(product.get("name", ""))
        text = PROMPT.format(product=product_name, size=size) + (PAIR_SCENE_NOTE if pair else "") \
            + (PACKAGING_NOTE.format(facts=facts) if facts else "") \
            + (FEEDBACK_NOTE.format(feedback=feedback) if feedback else "")
        content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_url(packshot)}},
        ]
    from babebasket import usage

    body = {
        **usage.REQUEST,
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
        usage.record("image_edit" if feedback else "image", None, model=body["model"], error=str(err),
                     images_in=len(content) - 1)
        return None
    usage.record("image_edit" if feedback else "image", reply, model=body["model"], images_in=len(content) - 1)
    image = _image_from_reply(reply or {})
    if image is None:
        text = ((reply or {}).get("choices") or [{}])[0].get("message", {}).get("content", "")
        print(f"scene: no image in reply: {str(text)[:160]}", file=sys.stderr)
    return image
