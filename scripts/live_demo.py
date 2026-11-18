"""Live Discord demo of the approval flow, stopping before Threads.

Real cards go to the approval channel and real taps/replies are read back. Uses its own
database (data/demo/live.db), so the real pipeline's drafts are untouched. Threads is
never called. The AI image model is replaced by pre-made images (each card says so),
so no image is paid for.

    python3 scripts/live_demo.py start --product 1669820       # LLM draft (~$0.01) + caption card
    python3 scripts/live_demo.py watch --scene A.png --edited B.png --minutes 9
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from babebasket import cli, config, db, discord, scene, threads  # noqa: E402
from babebasket.config import DATA  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_flow import load_product  # noqa: E402

DEMO = DATA / "demo"
DB = DEMO / "live.db"
NOTE = "🧪 Demo: pre-made image instead of a new AI call; nothing will be posted to Threads."


def start(product_id: str) -> None:
    cfg = config.load()
    conn = db.connect(DB)
    payload = cli.make_draft(load_product(product_id), cfg)
    draft_id = db.add_draft(conn, "dm", product_id, payload)
    print(f"draft #{draft_id} written (LLM cost ${payload['draft'].get('_usage', {}).get('cost', 0):.4f})")
    with mock.patch.object(cli, "DATA", DEMO):
        cli.cmd_post(cfg, conn)


def watch(scene_png: Path, edited_png: Path, minutes: float) -> None:
    cfg = config.load()
    conn = db.connect(DB)
    original_card = discord.build_image_card

    def fake_scene(packshot, product, cfg_, **kw):
        return (edited_png if kw.get("feedback") else scene_png).read_bytes()

    def card_with_note(*a, **kw):
        card = original_card(*a, **kw)
        card["content"] = (card["content"] + "\n" + NOTE)[:2000]
        return card

    def no_threads(*a, **k):
        raise AssertionError("demo must never post to Threads")

    last = {}
    deadline = time.time() + minutes * 60
    with mock.patch.object(cli, "DATA", DEMO), \
            mock.patch.object(scene, "generate", side_effect=fake_scene), \
            mock.patch.object(discord, "build_image_card", side_effect=card_with_note), \
            mock.patch.object(threads, "publish", side_effect=no_threads):
        while time.time() < deadline:
            cli.cmd_check(cfg, conn)
            cli.cmd_check_images(cfg, conn)
            cli.cmd_images(cfg, conn)
            states = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM drafts")}
            if states != last:
                print(time.strftime("%H:%M:%S"), "status:", states, flush=True)
                last = states
            if states and all(s in ("ready", "rejected") for s in states.values()):
                print("done: stopped before Threads")
                return
            time.sleep(15)
    print("watch time is up; run watch again to continue")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--product", default="1669820")
    w = sub.add_parser("watch")
    w.add_argument("--scene", type=Path, required=True)
    w.add_argument("--edited", type=Path, required=True)
    w.add_argument("--minutes", type=float, default=9)
    args = ap.parse_args()
    if args.cmd == "start":
        start(args.product)
    else:
        watch(args.scene, args.edited, args.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
