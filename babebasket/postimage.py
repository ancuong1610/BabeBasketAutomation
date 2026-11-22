"""Compose the post image from a scene, the round logo, and optional outlined text.

Product in the centre. Title (Vietnamese name + brand) and one subtitle line centred on
top, 2–3 points bottom-left, logo bottom-right. Text is white with a thin dark outline
and a soft shadow, no boxes, so it reads on any photo without a loud colour.
All Vietnamese text is drawn here, never by the image model.
"""

from __future__ import annotations

import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from babebasket.config import ROOT

W, H = 1080, 1350
WHITE = (255, 255, 255)
OUTLINE = (20, 20, 20)
MARGIN = 48
TOP = 56
TITLE_SIZE, SUBTITLE_SIZE, TEXT_SIZE = 54, 32, 33
TITLE_STROKE, TEXT_STROKE = 2, 1  # thin outline only, no shadow
TITLE_MAX_W = 960
SUBTITLE_MAX_W = 900
POINTS_MAX_W = 720
LINE_GAP = 6
POINT_GAP = 6
LOGO_SIZE = 150
FONT_BOLD = ROOT / "assets" / "fonts" / "BeVietnamPro-Bold.ttf"
FONT_TITLE = ROOT / "assets" / "fonts" / "BeVietnamPro-ExtraBold.ttf"
LOGO = ROOT / "assets" / "logo.jpg"
# Vertical band the product occupies (same brief as the Gemini prompt: 22%–74% of the height).
PRODUCT_ZONE = (int(H * 0.22), int(H * 0.74))
# Fallback scene: product centre x, top, max width, max height inside PRODUCT_ZONE.
PRODUCT_BOX = (W // 2, PRODUCT_ZONE[0], 560, PRODUCT_ZONE[1] - PRODUCT_ZONE[0])


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


def dhash(data: bytes) -> int:
    """64-bit difference hash: near-identical photos (re-crops, re-encodes) get hashes a few bits apart."""
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    return sum(1 << i for i, (a, b) in enumerate((px[r * 9 + c], px[r * 9 + c + 1])
                                                  for r in range(8) for c in range(8)) if a > b)


def wrap_balanced(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Same number of lines as a greedy wrap, but with the widest line as narrow as possible
    (no lonely last word under a long first line)."""
    greedy = _wrap(text, f, max_w)
    if len(greedy) < 2:
        return greedy
    lo, hi = int(max(f.getlength(w) for w in text.split())), max_w
    best = greedy
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = _wrap(text, f, mid)
        if len(trial) <= len(greedy):
            best, hi = trial, mid - 1
        else:
            lo = mid + 1
    return best


def _text(img: Image.Image, lines: list[str], f: ImageFont.FreeTypeFont, y: int, stroke: int,
          x: int | None = None) -> tuple[int, int, int, int]:
    """White outlined lines, centred when x is None. Returns the bounding box."""
    ascent, descent = f.getmetrics()
    line_h = ascent + descent + LINE_GAP
    widths = [f.getlength(l) for l in lines]
    xs = [int((W - w) / 2) if x is None else x for w in widths]
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((xs[i], y + i * line_h), line, font=f, fill=WHITE, stroke_width=stroke, stroke_fill=OUTLINE)
    x0 = min(xs) - stroke
    x1 = int(max(xx + w for xx, w in zip(xs, widths))) + stroke
    return (x0, y - stroke, x1, y + line_h * len(lines) - LINE_GAP + stroke)


def _logo() -> Image.Image:
    logo = Image.open(LOGO).convert("RGB").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    mask = Image.new("L", (LOGO_SIZE * 4, LOGO_SIZE * 4), 0)
    ImageDraw.Draw(mask).ellipse((6, 6, LOGO_SIZE * 4 - 6, LOGO_SIZE * 4 - 6), fill=255)
    logo.putalpha(mask.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS))
    return logo


def _place_logo(img: Image.Image) -> tuple[int, int, int, int]:
    logo = _logo()
    lx, ly = W - MARGIN - LOGO_SIZE, H - MARGIN - LOGO_SIZE
    img.paste(logo, (lx, ly), logo)
    return lx, ly, lx + LOGO_SIZE, ly + LOGO_SIZE


def render_clean(scene: Image.Image) -> Image.Image:
    """Fit the scene to the post canvas and add the logo."""
    img = ImageOps.fit(scene.convert("RGB"), (W, H), Image.LANCZOS)
    img.info["layout"] = {"logo": _place_logo(img)}
    return img


def render(scene: Image.Image, labels: dict) -> Image.Image:
    img = ImageOps.fit(scene.convert("RGB"), (W, H), Image.LANCZOS)
    layout: dict = {}

    y = TOP
    if labels.get("title"):
        f = font(TITLE_SIZE, title=True)
        layout["title"] = _text(img, wrap_balanced(labels["title"], f, TITLE_MAX_W), f, y, TITLE_STROKE)
        y = layout["title"][3] + 12
    if labels.get("subtitle"):
        f = font(SUBTITLE_SIZE)
        layout["subtitle"] = _text(img, wrap_balanced(labels["subtitle"], f, SUBTITLE_MAX_W), f, y, TEXT_STROKE)

    f = font(TEXT_SIZE)
    ascent, descent = f.getmetrics()
    line_h = ascent + descent + LINE_GAP
    wrapped = [_wrap(re.sub(r"^\s*[•▫️\-–—*·]+\s*", "", p), f, POINTS_MAX_W) for p in labels.get("points", []) if p]
    block_h = sum(len(w) for w in wrapped) * line_h - LINE_GAP + POINT_GAP * max(len(wrapped) - 1, 0)
    y = H - MARGIN - block_h
    layout["points"] = []
    for lines in wrapped:
        box = _text(img, lines, f, y, TEXT_STROKE, x=MARGIN)
        layout["points"].append(box)
        y = box[3] - TEXT_STROKE + LINE_GAP + POINT_GAP

    layout["logo"] = _place_logo(img)
    img.info["layout"] = layout
    return img


def last_layout(img: Image.Image) -> dict:
    """Box coordinates of a rendered image (for tests and debugging)."""
    return img.info.get("layout", {})


def side_by_side(left: bytes, right: bytes, gap: int = 40) -> bytes:
    """Two packshots on one white image, same height, e.g. shampoo + conditioner."""
    imgs = []
    for data in (left, right):
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        flat = Image.new("RGBA", img.size, WHITE + (255,))
        imgs.append(Image.alpha_composite(flat, img).convert("RGB"))
    h = max(i.height for i in imgs)
    imgs = [i.resize((max(1, round(i.width * h / i.height)), h), Image.LANCZOS) for i in imgs]
    canvas = Image.new("RGB", (sum(i.width for i in imgs) + gap, h), WHITE)
    canvas.paste(imgs[0], (0, 0))
    canvas.paste(imgs[1], (imgs[0].width + gap, 0))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


PICKER_TILE = 420
PICKER_GAP = 12
PICKER_GREEN = "#14834b"
PICKER_LABELS = {
    "lip_swatch": "swatch môi", "skin_swatch": "swatch da", "shade_range": "bảng màu",
    "texture": "chất thật", "application": "người dùng thử", "contents": "bên trong set",
    "label": "nhãn / thành phần", "product_hero": "ảnh gốc", "packshot": "ảnh gốc", "owner": "ảnh của bạn",
}


def render_picker_sheet(candidates: list[dict], selected: list[int], photos: list[bytes | None]) -> bytes:
    """Draw fixed, three-column tiles so the reaction numbers stay readable in previews."""
    columns = 3
    rows = (len(candidates) + columns - 1) // columns
    tile_height = PICKER_TILE + 58
    sheet = Image.new("RGB", (columns * (PICKER_TILE + PICKER_GAP) + PICKER_GAP,
                              rows * (tile_height + PICKER_GAP) + PICKER_GAP), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    for number, (candidate, photo) in enumerate(zip(candidates, photos), 1):
        row, column = divmod(number - 1, columns)
        x = PICKER_GAP + column * (PICKER_TILE + PICKER_GAP)
        y = PICKER_GAP + row * (tile_height + PICKER_GAP)
        draw.rectangle((x, y, x + PICKER_TILE - 1, y + tile_height - 1), fill="white")
        if photo:
            with Image.open(io.BytesIO(photo)) as image:
                image.thumbnail((PICKER_TILE - 24, PICKER_TILE - 24), Image.Resampling.LANCZOS)
                sheet.paste(image, (x + (PICKER_TILE - image.width) // 2, y + (PICKER_TILE - image.height) // 2))
        else:
            draw.text((x + PICKER_TILE // 2, y + PICKER_TILE // 2), "Ảnh chưa tải được",
                      font=font(28), fill="#474747", anchor="mm")
        kind = candidate["source"] if candidate["source"] == "owner" else candidate["type"]
        draw.text((x + PICKER_TILE // 2, y + PICKER_TILE + 19), PICKER_LABELS.get(kind, "ảnh shop"),
                  font=font(31), fill="#202332", anchor="mm")
        draw.ellipse((x + 20, y + 20, x + 108, y + 108), fill="#202332")
        draw.text((x + 64, y + 64), str(number), font=font(62, title=True), fill="white", anchor="mm")
        if number in selected:
            draw.rectangle((x, y, x + PICKER_TILE - 1, y + tile_height - 1), outline=PICKER_GREEN, width=12)
            right = x + PICKER_TILE
            draw.rounded_rectangle((right - 101, y + 20, right - 20, y + 101), radius=14, fill=PICKER_GREEN)
            draw.line([(right - 82, y + 61), (right - 66, y + 77), (right - 39, y + 45)],
                      fill="white", width=9, joint="curve")
    output = io.BytesIO()
    sheet.save(output, "JPEG", quality=90)
    return output.getvalue()


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
