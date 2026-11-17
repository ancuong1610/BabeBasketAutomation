"""The post image: a scene (Gemini photo or plain fallback) + red text boxes + logo.

One template, modelled on the shop's Mixa post: title box centred on top, key
ingredient and "Dành cho …" on the right, 2–3 points stacked bottom-left, logo
sticker bottom-right. All Vietnamese text is drawn here, never by the image model.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from babebasket.config import ROOT

W, H = 1080, 1350
RED = (224, 38, 43)
WHITE = (255, 255, 255)
RADIUS = 18
PAD_X, PAD_Y = 18, 12
MAX_TEXT_W = 390        # right-column boxes: stay right of PRODUCT_BOX
TITLE_MAX_W = 900
POINTS_MAX_W = 700
MARGIN = 48
GAP = 18
LOGO_SIZE = 180
TITLE_SIZE, TEXT_SIZE = 48, 34
FONT_BOLD = ROOT / "assets" / "fonts" / "BeVietnamPro-Bold.ttf"
FONT_TITLE = ROOT / "assets" / "fonts" / "BeVietnamPro-ExtraBold.ttf"
LOGO = ROOT / "assets" / "logo.jpg"
# Where the product sits: centre x, top, max width, max height. Left of centre so the
# right column and the bottom row stay free for text (same brief as the Gemini prompt).
PRODUCT_BOX = (380, 220, 440, 860)


@lru_cache(maxsize=None)
def font(size: int, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_TITLE if title else FONT_BOLD), size)


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if f.getlength(trial) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _box_size(text: str, f: ImageFont.FreeTypeFont, max_w: int = MAX_TEXT_W) -> tuple[list[str], int, int, int]:
    lines = _wrap(text, f, max_w)
    ascent, descent = f.getmetrics()
    line_h = ascent + descent
    w = int(max(f.getlength(l) for l in lines)) + 2 * PAD_X
    h = line_h * len(lines) + 2 * PAD_Y
    return lines, w, h, line_h


def _draw_box(img: Image.Image, x: int, y: int, lines: list[str], w: int, h: int, line_h: int,
              f: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    # soft shadow so the box lifts off busy backgrounds
    shadow = Image.new("L", img.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((x + 3, y + 6, x + w + 3, y + h + 6), RADIUS, fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    img.paste((0, 0, 0), (0, 0), shadow)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + w, y + h), RADIUS, fill=RED)
    for i, line in enumerate(lines):
        d.text((x + PAD_X, y + PAD_Y + i * line_h), line, font=f, fill=WHITE)
    return (x, y, x + w, y + h)


def _logo() -> Image.Image:
    logo = Image.open(LOGO).convert("RGB").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    mask = Image.new("L", (LOGO_SIZE * 4, LOGO_SIZE * 4), 0)
    ImageDraw.Draw(mask).ellipse((6, 6, LOGO_SIZE * 4 - 6, LOGO_SIZE * 4 - 6), fill=255)
    logo.putalpha(mask.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS))
    return logo


def render(scene: Image.Image, labels: dict) -> Image.Image:
    img = ImageOps.fit(scene.convert("RGB"), (W, H), Image.LANCZOS)
    layout: dict = {}

    if labels.get("title"):
        f = font(TITLE_SIZE, title=True)
        lines, w, h, lh = _box_size(labels["title"], f, TITLE_MAX_W)
        layout["title"] = _draw_box(img, (W - w) // 2, 64, lines, w, h, lh, f)

    f = font(TEXT_SIZE)
    y = (layout["title"][3] if "title" in layout else MARGIN) + 2 * GAP + 40
    for key in ("hero", "who_for"):
        if labels.get(key):
            lines, w, h, lh = _box_size(labels[key], f)
            layout[key] = _draw_box(img, W - MARGIN - w, y, lines, w, h, lh, f)
            y = layout[key][3] + GAP

    sized = [_box_size(p, f, POINTS_MAX_W) for p in labels.get("points", []) if p]
    y = H - MARGIN - sum(s[2] for s in sized) - GAP * max(len(sized) - 1, 0)
    layout["points"] = []
    for lines, w, h, lh in sized:
        layout["points"].append(_draw_box(img, MARGIN, y, lines, w, h, lh, f))
        y += h + GAP

    logo = _logo()
    lx, ly = W - MARGIN - LOGO_SIZE, H - MARGIN - LOGO_SIZE
    img.paste(logo, (lx, ly), logo)
    layout["logo"] = (lx, ly, lx + LOGO_SIZE, ly + LOGO_SIZE)

    img.info["layout"] = layout
    return img


def last_layout(img: Image.Image) -> dict:
    """Box coordinates of a rendered image (for tests and debugging)."""
    return img.info.get("layout", {})


def fallback_scene(packshot: bytes) -> Image.Image:
    """Warm cream gradient with the packshot in the middle (white background blended away)."""
    top, bottom = (248, 242, 234), (233, 220, 204)
    grad = Image.new("RGB", (1, H))
    for yy in range(H):
        t = yy / (H - 1)
        grad.putpixel((0, yy), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    bg = grad.resize((W, H))
    product = Image.open(io.BytesIO(packshot)).convert("RGBA")
    flat = Image.new("RGBA", product.size, WHITE + (255,))
    product = Image.alpha_composite(flat, product).convert("RGB")
    product.thumbnail(PRODUCT_BOX[2:], Image.LANCZOS)
    layer = Image.new("RGB", (W, H), WHITE)
    cx, top, bw, bh = PRODUCT_BOX
    layer.paste(product, (cx - product.width // 2, top + (bh - product.height) // 2))
    return ImageChops.multiply(bg, layer)


def save(img: Image.Image, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=90, optimize=True)
    return path
