"""Compare OpenRouter image models on our real job: put a dm packshot in a hand in a store aisle.

Every model gets the same packshots and the same prompt (babebasket/scene.py). Output:
  data/model_compare/<timestamp>/sheet.jpg     one row per model, packshot row on top
  data/model_compare/<timestamp>/results.json  cost (from OpenRouter), seconds, errors
  data/model_compare/<timestamp>/<model>__<product>.png

Usage:
  python3 scripts/compare_image_models.py                       # default models and products
  python3 scripts/compare_image_models.py --models a/b c/d --products 1669820 1633046
Costs real money: about (sum of model prices) x products. The defaults are about $0.80.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from babebasket import scene  # noqa: E402
from babebasket.config import DATA, ROOT, secret  # noqa: E402
from babebasket.http import request_json  # noqa: E402
from babebasket.llm import OPENROUTER_URL  # noqa: E402
from babebasket.stores import dm  # noqa: E402

DEFAULT_MODELS = [
    "google/gemini-3.1-flash-image",       # current
    "google/gemini-nano-banana-2.1",
    "google/gemini-3.1-flash-lite-image",
    "bytedance-seed/seedream-5-0-flash",
    "bytedance-seed/seedream-5-5",
    "qwen/qwen-image-3",
    "openai/gpt-image-2.6-sunburst",
]
DEFAULT_PRODUCTS = ["1669820", "1633046", "3115734"]  # Mixa tub, Elvital bottle, Garnier spray
TILE_W, TILE_H, LABEL_W = 320, 400, 300


def modalities(model: str) -> list[str]:
    # Gemini/GPT "chat" image models answer with text too; pure image models only with images.
    return ["image", "text"] if model.startswith(("google/", "openai/gpt-5")) else ["image"]


def fetch_bytes(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (babebasket)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def load_product(dan: str) -> dict:
    raw = request_json(dm.DETAIL_URL.format(dan=dan))
    detail = dm.parse_detail(raw)
    return {
        "product_id": dan,
        "brand": raw.get("brand", {}).get("name", ""),
        "name": raw.get("title", {}).get("headline", ""),
        "packshot": fetch_bytes(detail["images"][0]),
    }


def _image_from_reply(reply: dict) -> bytes | None:
    found = scene._image_from_reply(reply)
    if found:
        return found
    # some providers return a plain https URL instead of a data URL
    for choice in reply.get("choices") or []:
        for img in (choice.get("message") or {}).get("images") or []:
            url = (img.get("image_url") or {}).get("url", "")
            if url.startswith("http"):
                return fetch_bytes(url)
    return None


def run_one(model: str, product: dict, key: str) -> dict:
    prompt = scene.PROMPT.format(product=f"{product['brand']} {product['name']}")
    packshot_url = f"data:{scene._mime(product['packshot'])};base64," + base64.b64encode(product["packshot"]).decode()
    body = {
        "model": model,
        "modalities": modalities(model),
        "image_config": {"aspect_ratio": "4:5"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": packshot_url}},
        ]}],
    }
    started = time.time()
    result = {"model": model, "product": product["product_id"]}
    try:
        reply = request_json(OPENROUTER_URL, method="POST", body=body, timeout=240, retries=1,
                             headers={"Authorization": f"Bearer {key}", "X-Title": "babe basket model test"})
        result["cost"] = (reply.get("usage") or {}).get("cost")
        result["provider"] = reply.get("provider")
        image = _image_from_reply(reply)
        if not image:
            text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content")
            raise RuntimeError(f"no image in reply: {str(text)[:150]}")
        result["image"] = image
    except Exception as err:
        result["error"] = str(err)[:300]
    result["seconds"] = round(time.time() - started, 1)
    return result


def tile(data: bytes | None, text: str = "") -> Image.Image:
    canvas = Image.new("RGB", (TILE_W, TILE_H), (40, 40, 40))
    if data:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((TILE_W, TILE_H))
        canvas.paste(img, ((TILE_W - img.width) // 2, (TILE_H - img.height) // 2))
    if text:
        ImageDraw.Draw(canvas).multiline_text((10, 10), text, fill=(255, 120, 120), font=_font(16))
    return canvas


def _font(size: int):
    return ImageFont.truetype(str(ROOT / "assets" / "fonts" / "BeVietnamPro-Bold.ttf"), size)


def contact_sheet(models: list[str], products: list[dict], results: list[dict]) -> Image.Image:
    by = {(r["model"], r["product"]): r for r in results}
    rows = [("packshot (input)", [p["packshot"] for p in products], "")]
    for m in models:
        rs = [by[(m, p["product_id"])] for p in products]
        costs = [r["cost"] for r in rs if r.get("cost") is not None]
        secs = [r["seconds"] for r in rs]
        ok = sum(1 for r in rs if r.get("image"))
        stats = (f"${sum(costs) / len(costs):.3f}/img" if costs else "cost ?") + f"\n{sum(secs) / len(secs):.0f}s avg\n{ok}/{len(rs)} ok"
        rows.append((m, [r.get("image") for r in rs], stats))
    sheet = Image.new("RGB", (LABEL_W + TILE_W * len(products), TILE_H * len(rows)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, (label, images, stats) in enumerate(rows):
        y = i * TILE_H
        draw.multiline_text((12, y + 14), label.replace("/", "/\n"), fill=(255, 255, 255), font=_font(22))
        draw.multiline_text((12, y + 120), stats, fill=(180, 220, 255), font=_font(20))
        for j, data in enumerate(images):
            err = "" if data else "failed:\n" + "\n".join(
                by.get((label, products[j]["product_id"]), {}).get("error", "")[k:k + 32] for k in range(0, 160, 32))
            sheet.paste(tile(data, err), (LABEL_W + j * TILE_W, y))
    return sheet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--products", nargs="+", default=DEFAULT_PRODUCTS, help="dm product numbers (DAN)")
    args = ap.parse_args()

    key = secret("OPENROUTER_API_KEY")
    out = DATA / "model_compare" / dt.datetime.now().strftime("%Y%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    products = [load_product(d) for d in args.products]
    print(f"{len(args.models)} models x {len(products)} products -> {out}")

    jobs = [(m, p) for m in args.models for p in products]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda job: run_one(job[0], job[1], key), jobs))

    for r in results:
        name = f"{r['model'].replace('/', '_')}__{r['product']}"
        if r.get("image"):
            Image.open(io.BytesIO(r["image"])).save(out / f"{name}.png")
        status = "ok" if r.get("image") else f"FAILED {r.get('error', '')[:90]}"
        cost = f"${r['cost']:.4f}" if r.get("cost") is not None else "  ?   "
        print(f"{r['model']:40} {r['product']}  {cost}  {r['seconds']:5.1f}s  {status}")

    contact_sheet(args.models, products, results).save(out / "sheet.jpg", quality=88)
    (out / "results.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "image"} for r in results], indent=1))
    total = sum(r["cost"] for r in results if r.get("cost"))
    print(f"total cost ${total:.3f} · sheet: {out / 'sheet.jpg'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
