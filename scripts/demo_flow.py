"""Mock end-to-end demo: one real dm product through the real pipeline code, stopping before Threads.

Real:      dm product data, reviews, LLM caption draft (about $0.01), pricing, card building,
           image rendering, approval logic and database states.
Simulated: Discord (nothing is sent; the owner's taps and replies are scripted),
           the AI image model (pre-made images are used, no paid call), Threads (never called).

    python3 scripts/demo_flow.py --scene data/model_compare/<run>/google_gemini-3.1-flash-image__1669820.png \\
        --edited data/model_compare/<run>/google_gemini-nano-banana-2.1__1669820.png

Writes data/demo/demo.html: every Discord message in order, as the owner would see it.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from babebasket import cli, config, db, discord, scene, threads  # noqa: E402
from babebasket.config import DATA  # noqa: E402
from babebasket.http import request_json  # noqa: E402
from babebasket.stores import dm  # noqa: E402

EDIT_NOTE = "sản phẩm to hơn, nền sáng hơn"


def load_product(dan: str) -> dict:
    raw = request_json(dm.DETAIL_URL.format(dan=dan))
    rating = request_json(dm.RATINGS_URL.format(ids=dan))[0]
    return {
        "store": "dm", "product_id": dan,
        "brand": raw.get("brand", {}).get("name", ""),
        "name": raw.get("title", {}).get("headline", ""),
        "price_eur": dm.parse_price(raw.get("price", {}).get("price", {}).get("current", {}).get("value", "")),
        "rating": rating["ratingAvg"], "review_count": rating["ratingCount"],
        "categories": "", "image_url": "", "product_url": dm.SITE + raw.get("self", ""),
    }


class Timeline:
    def __init__(self):
        self.steps: list[dict] = []

    def add(self, kind: str, title: str, **kw):
        self.steps.append({"kind": kind, "title": title, **kw})
        print(f"[{kind}] {title}")


def run(dan: str, scene_png: Path, edited_png: Path) -> tuple[Timeline, dict]:
    t = Timeline()
    cfg = config.load()
    cfg["discord"] = {"channel_id": "DEMO", "owner_user_ids": []}
    cfg.setdefault("image", {})["enabled"] = True
    conn = db.connect(":memory:")

    # 1. product (real)
    product = load_product(dan)
    t.add("real", "1 · Product picked from dm", text=f"{product['brand']} · {product['name']} · €{product['price_eur']:.2f} · "
          f"⭐ {product['rating']:.2f} ({product['review_count']} reviews)")

    # 2. draft (real LLM)
    payload = cli.make_draft(product, cfg)
    draft_id = db.add_draft(conn, "dm", dan, payload)
    usage = payload["draft"].get("_usage", {})
    t.add("real", "2 · LLM wrote the draft (Gemini 3.8 Flash via OpenRouter)",
          text=f"{len(payload['draft'].get('captions', []))} captions, image labels, review summary · "
               f"cost ${usage.get('cost', 0):.4f}")

    scene_calls = []

    def fake_scene(packshot, prod, cfg_, **kw):
        scene_calls.append({"feedback": kw.get("feedback", ""), "edits_previous_image": bool(kw.get("previous"))})
        return (edited_png if kw.get("feedback") else scene_png).read_bytes()

    def fake_post_card(channel, card, n, image_path=None):
        t.add("bot", "3 · Caption card in #approvals", card=card)
        return "CAPTION-MSG"

    def fake_post_image_card(channel, card, path, edits_left):
        n = sum(1 for s in t.steps if s.get("image_path"))
        t.add("bot", f"{5 if n == 0 else 7} · Image card" + (" (after your edit)" if n else ""),
              card=card, image_path=path, reactions=["✅", "❌"] + (["✏️"] if edits_left else []))
        return f"IMG-MSG-{n + 1}"

    def fake_reply(channel, message_id, text):
        t.add("bot", "Bot reply", text=text)

    def no_threads(*a, **k):
        raise AssertionError("demo must never post to Threads")

    image_taps = iter([None, "accept"])
    feedback = iter([{"text": EDIT_NOTE, "last_id": "R1", "unreadable": False},
                     {"text": "", "last_id": None, "unreadable": False}])

    with mock.patch.object(discord, "post_card", side_effect=fake_post_card), \
            mock.patch.object(discord, "post_image_card", side_effect=fake_post_image_card), \
            mock.patch.object(discord, "reply", side_effect=fake_reply), \
            mock.patch.object(discord, "read_decision", return_value={"status": "approved", "caption": 2, "multiple": False}), \
            mock.patch.object(discord, "read_image_decision", side_effect=lambda *a, **k: next(image_taps)), \
            mock.patch.object(discord, "read_image_feedback", side_effect=lambda *a, **k: next(feedback)), \
            mock.patch.object(scene, "generate", side_effect=fake_scene), \
            mock.patch.object(threads, "publish", side_effect=no_threads):
        cli.cmd_post(cfg, conn)                                   # 3
        t.add("you", "4 · You tap 2️⃣ then ✅ on the caption card (simulated)")
        cli.cmd_check(cfg, conn)                                  # caption approved
        cli.cmd_images(cfg, conn)                                 # 5: AI image + image card
        t.add("you", f"6 · You reply to the image card: “{EDIT_NOTE}” (simulated)")
        cli.cmd_check_images(cfg, conn)                           # 7: edited image card
        t.add("you", "8 · You tap ✅ on the new image card (simulated)")
        cli.cmd_check_images(cfg, conn)                           # ready

    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    final = json.loads(row["payload"])
    caption = final["draft"]["captions"][(row["chosen_caption"] or 1) - 1]["text"]
    t.add("stop", f"9 · Status: {row['status']} · STOPPED before Threads",
          text="This is what would be posted to Threads (not sent):", caption=caption, image_path=final["image_path"])
    info = {"status": row["status"], "image_model_calls_simulated": scene_calls, "llm_cost": usage.get("cost")}
    return t, info


def md(text: str) -> str:
    out = html.escape(text or "")
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return out.replace("\n", "<br>")


def img_tag(path: str | None, url: str | None = None) -> str:
    if path:
        data = base64.b64encode(Path(path).read_bytes()).decode()
        return f'<img class="shot" src="data:image/jpeg;base64,{data}">'
    if url:
        return f'<img class="shot" src="{html.escape(url)}">'
    return ""


def render_html(t: Timeline, info: dict) -> str:
    badges = {"real": ("REAL", "#3ba55d"), "bot": ("BOT · #approvals", "#5865f2"),
              "you": ("YOU · simulated", "#faa61a"), "stop": ("STOP", "#ed4245")}
    parts = []
    for s in t.steps:
        label, color = badges[s["kind"]]
        body = ""
        if s.get("text"):
            body += f'<p>{md(s["text"])}</p>'
        card = s.get("card")
        if card:
            body += f'<p>{md(card.get("content", ""))}</p>'
            for e in card.get("embeds", []):
                col = f'#{e.get("color", 0x888888):06x}'
                fields = "".join(f'<div class="f"><b>{html.escape(f["name"])}</b><br>{md(f["value"])}</div>'
                                 for f in e.get("fields", []))
                url = (e.get("image") or {}).get("url", "")
                pic = img_tag(s.get("image_path")) if url.startswith("attachment://") else img_tag(None, url)
                body += (f'<div class="embed" style="border-color:{col}"><div class="et">{html.escape(e.get("title", ""))}</div>'
                         f'<div>{md(e.get("description", ""))}</div><div class="fields">{fields}</div>{pic}</div>')
            if s.get("reactions"):
                body += '<div class="r">' + " ".join(f"<span>{r} 1</span>" for r in s["reactions"]) + "</div>"
        elif s.get("caption"):
            body += f'<div class="embed" style="border-color:#ed4245"><div>{md(s["caption"])}</div>{img_tag(s.get("image_path"))}</div>'
        parts.append(f'<section><div class="badge" style="background:{color}">{label}</div><h2>{html.escape(s["title"])}</h2>{body}</section>')
    note = (f"LLM cost ${info['llm_cost'] or 0:.4f} · image model was <b>not</b> called (pre-made images from the "
            f"model test were used; the second one stands in for the edited image) · Discord and Threads not contacted.")
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>babe basket flow demo</title><style>
body{{background:#313338;color:#dbdee1;font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
main{{max-width:720px;margin:auto}} h1{{color:#fff;font-size:22px}} .note{{color:#b5bac1;font-size:13px}}
section{{background:#2b2d31;border-radius:8px;padding:14px 16px;margin:14px 0}}
h2{{font-size:16px;color:#fff;margin:6px 0 8px}} .badge{{display:inline-block;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px}}
.embed{{background:#232428;border-left:4px solid;border-radius:4px;padding:10px 12px;margin-top:8px}} .et{{font-weight:700;color:#00a8fc;margin-bottom:6px}}
.fields{{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:8px;font-size:13px}} .f{{min-width:140px}}
.shot{{max-width:100%;border-radius:6px;margin-top:10px;display:block}} .r span{{display:inline-block;background:#383a40;border-radius:8px;padding:2px 8px;margin:8px 4px 0 0}}
</style></head><body><main><h1>babe basket · approval flow demo</h1><p class="note">{note}</p>{''.join(parts)}</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", default="1669820")
    ap.add_argument("--scene", type=Path, required=True, help="pre-made AI scene for the first image")
    ap.add_argument("--edited", type=Path, required=True, help="pre-made image standing in for the edited one")
    ap.add_argument("--out", type=Path, default=DATA / "demo" / "demo.html")
    args = ap.parse_args()
    with mock.patch.object(cli, "DATA", args.out.parent):
        t, info = run(args.product, args.scene, args.edited)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_html(t, info), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, default=str))
    print(f"demo: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
