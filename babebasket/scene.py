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


# K1 "flat lay chéo" from Lab D (2026-11-22, Gemini web): 4.45 overall, 4.62 without the pair row;
# best style for the serum (20/20). Rewritten from the Lab D report; the original lives in
# styles.json on the MacBook. [image] style = "k1" in config.toml picks it; "k0" = PROMPT above.
PROMPT_K1 = """Create a vertical 4:5 photo for an Instagram post of a small German drugstore reseller.

Use the product in the attached image: {product}.
Scene: a styled top-down flat lay shot CLOSE UP, camera low above the product so the product is \
BIG and clearly the hero of the photo. The product lies on a smooth seamless surface, rotated diagonally \
about 30 degrees. The background is a soft pastel colour taken from the product's own packaging \
(for example light blue for a blue label, blush pink for a pink one, deep navy for a navy tin). \
A few small props that fit the product and match its colours: water droplets, a corner of a soft \
folded towel, two or three petals. Soft diffused daylight from the upper left, gentle natural shadow, \
realistic high-end product photography, slightly saturated colours.

Hard rules:
- The product must look exactly like the attached image: same shape, colours, brand, logo and label text. \
Do not redesign, translate or invent anything on the packaging. Exactly as many items as in the attached image.
- Product sharp, front label facing the camera and readable, fully visible, not cropped.
- SIZE: the product is large. It fills about 60-70% of the image width (a tall bottle or tube instead \
fills almost the whole band between 22% and 74% of the image height). A small product far away on a big \
empty background is wrong.
- Put the product exactly in the CENTRE of the image, horizontally and vertically within that band. \
Props are few and small, only at the left and right edges, partly cut off by the frame.
- Keep the top 20% and the bottom 24% of the image calm (plain background colour, no props), \
because text will be added there later.
- No hands, no people. Do not add any text, captions, stickers, logos, watermarks or price tags."""

PAIR_SCENE_NOTE_K1 = (
    "\n\nThe attached image shows TWO products: a shampoo and its matching conditioner, sold as a set. "
    "Show BOTH bottles, lying next to each other on the surface, both front labels fully visible and "
    "unchanged. Two bottles, not one. Keep their true relative sizes as in the attached image. "
    "Together they take the central product area described above."
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
        if image_cfg.get("style", "k0") == "k1":
            text = PROMPT_K1.format(product=product_name) + (PAIR_SCENE_NOTE_K1 if pair else "")
        else:
            size = "" if pair else size_hint(product.get("name", ""))
            text = PROMPT.format(product=product_name, size=size) + (PAIR_SCENE_NOTE if pair else "")
        text += (PACKAGING_NOTE.format(facts=facts) if facts else "") \
            + (FEEDBACK_NOTE.format(feedback=feedback) if feedback else "")
        content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_url(packshot)}},
        ]
    if image_cfg.get("backend", "openrouter") == "gemini_web":  # free: the owner's Gemini in Chrome
        from babebasket import gemini_web

        text = content[0]["text"].replace("4:5", gemini_web._cfg(cfg)["ratio"])
        images = [previous, packshot] if feedback and previous else [packshot]
        return gemini_web.generate_image(images, text, cfg)
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


# --- supporting photos (makeup) -----------------------------------------------------------
PHOTO_TYPES = ("product_hero", "product_detail", "shade_range", "skin_swatch", "lip_swatch", "texture",
               "application", "duplicate", "irrelevant", "uncertain")
SUPPORT_PROMPT = """These photos come from the shop page of one product: {product}.
Label every photo, numbered in the order given (1 = first photo). Types:
product_hero (the pack itself), product_detail (close-up of the pack or applicator), shade_range (many shades
side by side), skin_swatch (colour on arm/hand skin), lip_swatch (colour on lips), texture (the product itself
smeared or close up), application (a person wearing or applying it), duplicate (same as an earlier photo),
irrelevant (logos, text-only, unrelated), uncertain.
For swatch, texture and application photos also say whether they show THIS product's shade ({shade}):
true, false (another shade), or null (cannot tell / no shade named).
Reply with JSON only: {{"photos": [{{"i": 1, "type": "...", "this_shade": true}}]}}"""
SUPPORT_ORDER = ("lip_swatch", "skin_swatch", "application", "texture", "shade_range")  # most useful first
MAX_SUPPORTING = 3


def shade_of(name: str) -> str:
    """'Lipgloss Lifter Gloss 005 Petal, 5,4 ml' -> '005 Petal'; '' when the name has no shade number."""
    m = re.search(r"\b(\d{2,3}[A-Za-z]?(?:\s+[A-ZÄÖÜ][\wäöüß-]*){0,3})\s*,", name or "")
    return m.group(1).strip() if m else ""


def pick_supporting(labels: list[dict], photos: list[str]) -> list[str]:
    """Which shop photos go into the post after the AI image: by type, never another shade, at most 3."""
    by_type: dict[str, list[str]] = {}
    for item in labels:
        try:
            i = int(item.get("i")) - 1
        except (TypeError, ValueError):
            continue
        kind = item.get("type")
        if not 0 <= i < len(photos) or kind not in SUPPORT_ORDER:
            continue
        if kind != "shade_range" and item.get("this_shade") is False:
            continue  # a swatch of another shade would mislead the buyer
        by_type.setdefault(kind, []).append(photos[i])
    out: list[str] = []
    for kind in SUPPORT_ORDER:
        for url in by_type.get(kind, [])[: 1 if kind == "shade_range" else 2]:
            if url not in out:
                out.append(url)
    return out[:MAX_SUPPORTING]


def select_supporting(product: dict, cfg: dict, fetch: Callable[[str], bytes], post: Callable[..., dict] = request_json,
                      api_key: str | None = None) -> list[str]:
    """One vision call over the product page's extra photos (small JPEGs); returns the photo links to post."""
    photos = (product.get("gallery") or [])[1:7]  # the first is the packshot the AI image is made from
    if not photos:
        return []
    images = [fetch(small_jpg(u, 400)) for u in photos]
    data = _vision_json(SUPPORT_PROMPT.format(product=f"{product.get('brand', '')} {product.get('name', '')}",
                                              shade=shade_of(product.get("name", "")) or "not named"),
                        images, cfg, post, api_key, stage="gallery")
    return [small_jpg(u, 1200) for u in pick_supporting(data.get("photos") or [], photos)]


def small_jpg(url: str, size: int) -> str:
    """dm image link as a JPEG of at most size x size (Threads takes JPEG/PNG, not the WebP/AVIF of f_auto)."""
    return re.sub(r"f_auto,q_auto,c_fit,h_\d+,w_\d+", f"f_jpg,q_auto,c_fit,h_{size},w_{size}", url)
