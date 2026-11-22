"""Owner controls that cost nothing: 🖼️ new product photo before ✅, 🔤 new words on the image; and cost tracking."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from babebasket import cli, db, discord, usage

CFG = {"discord": {"channel_id": "CH", "owner_user_ids": []},
       "image": {"enabled": True, "max_per_day": 4, "max_per_post": 2}}


def jpg(size=(600, 800), color="pink") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def pending_draft(conn, pair=False, card="100"):
    product = {"store": "dm", "product_id": "s1", "brand": "B", "name": "Shampoo X", "image_url": "https://dm/s1.jpg"}
    if pair:
        product["pair"] = {"product_id": "c1", "name": "Conditioner X", "image_url": "https://dm/c1.jpg"}
    payload = {"product": product, "quote": {}, "warnings": [], "packaging_facts": "old facts",
               "draft": {"captions": [{"style": "a", "text": "x"}],
                         "labels": {"title": "Tóc mượt", "subtitle": "Tóc khô", "points": ["Một", "Hai"]}}}
    draft_id = db.add_draft(conn, "dm", "s1", payload)
    db.set_status(conn, draft_id, "pending", discord_message_id=card)
    return draft_id


class SourcePhotoTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.tmp = tempfile.TemporaryDirectory()
        self.data = mock.patch.object(cli, "DATA", Path(self.tmp.name))
        self.data.start()
        self.replies = []
        self.reply = mock.patch.object(discord, "reply", side_effect=lambda ch, mid, t: self.replies.append(t))
        self.reply.start()

    def tearDown(self):
        self.reply.stop()
        self.data.stop()
        self.tmp.cleanup()

    def payload(self, draft_id):
        return json.loads(self.conn.execute("SELECT payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0])

    def run_replies(self, messages, fetched):
        with mock.patch.object(discord, "replies_after", return_value=messages), \
                mock.patch.object(cli, "fetch_bytes", side_effect=lambda url: fetched[url]):
            cli.cmd_source_replies(CFG, self.conn)

    def test_attached_photo_replaces_the_product_photo(self):
        draft_id = pending_draft(self.conn)
        msg = {"id": "200", "content": "", "author": {"id": "7"}, "message_reference": {"message_id": "100"},
               "attachments": [{"url": "https://cdn.discord/a.png?ex=1", "filename": "a.png", "content_type": "image/png"}]}
        self.run_replies([msg], {"https://cdn.discord/a.png?ex=1": jpg()})
        product = self.payload(draft_id)["product"]
        self.assertTrue(product["image_url"].startswith("file://"))
        self.assertEqual(product["source_original"], "https://dm/s1.jpg")
        self.assertEqual(product["source_replaced_from"], "https://cdn.discord/a.png")
        self.assertNotIn("packaging_facts", self.payload(draft_id))
        self.assertIn("Đã thay ảnh gốc", self.replies[0])
        # the new photo is what the image is made from (file:// works with the normal downloader)
        self.assertEqual(Image.open(io.BytesIO(cli.fetch_bytes(product["image_url"]))).size, (600, 800))
        status = self.conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0]
        self.assertEqual(status, "pending")

    def test_link_with_xa_replaces_the_conditioner_photo(self):
        draft_id = pending_draft(self.conn, pair=True)
        msg = {"id": "201", "content": "ảnh xả https://shop/c.jpg", "author": {"id": "7"},
               "message_reference": {"message_id": "100"}}
        self.run_replies([msg], {"https://shop/c.jpg": jpg()})
        product = self.payload(draft_id)["product"]
        self.assertEqual(product["image_url"], "https://dm/s1.jpg")
        self.assertTrue(product["pair"]["image_url"].startswith("file://"))
        self.assertIn("dầu xả", self.replies[0])

    def test_bad_photos_are_refused_and_explained(self):
        draft_id = pending_draft(self.conn)
        msgs = [{"id": "202", "content": "https://shop/tiny.jpg", "author": {"id": "7"},
                 "message_reference": {"message_id": "100"}},
                {"id": "203", "content": "https://shop/page.html.jpg", "author": {"id": "7"},
                 "message_reference": {"message_id": "100"}}]
        self.run_replies(msgs, {"https://shop/tiny.jpg": jpg((100, 100)), "https://shop/page.html.jpg": b"<html>"})
        self.assertEqual(self.payload(draft_id)["product"]["image_url"], "https://dm/s1.jpg")
        self.assertIn("quá nhỏ", self.replies[0])
        self.assertIn("không phải file ảnh", self.replies[1])

    def test_text_replies_bots_and_other_cards_are_ignored_and_not_read_twice(self):
        draft_id = pending_draft(self.conn)
        msgs = [{"id": "204", "content": "đẹp đó", "author": {"id": "7"}, "message_reference": {"message_id": "100"}},
                {"id": "205", "content": "https://x/y.jpg", "author": {"id": "1", "bot": True},
                 "message_reference": {"message_id": "100"}},
                {"id": "206", "content": "https://x/y.jpg", "author": {"id": "7"},
                 "message_reference": {"message_id": "99"}}]
        self.run_replies(msgs, {})
        self.assertEqual(self.payload(draft_id)["product"]["image_url"], "https://dm/s1.jpg")
        self.assertEqual(self.replies, [])
        with mock.patch.object(discord, "replies_after", return_value=[]) as after:
            cli.cmd_source_replies(CFG, self.conn)
        self.assertEqual(after.call_args.args[1], "206")


class TextOnlyEditTest(unittest.TestCase):
    def test_reply_lines_become_title_and_points(self):
        current = {"title": "Cũ", "subtitle": "Phụ", "points": ["a", "b"], "kind": "other"}
        self.assertEqual(cli.labels_from_reply("Tóc mềm cả ngày\n• Ý một\n- Ý hai", current),
                         {"title": "Tóc mềm cả ngày", "points": ["Ý một", "Ý hai"]})
        self.assertEqual(cli.labels_from_reply("Chỉ tiêu đề", current), {"title": "Chỉ tiêu đề"})

    def test_text_tap_and_reply_reletters_without_ai(self):
        conn = db.connect(":memory:")
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp) / "scene.png"
            Image.new("RGB", (1080, 1350), "white").save(scene)
            draft_id = pending_draft(conn)
            payload = json.loads(conn.execute("SELECT payload FROM drafts").fetchone()[0])
            payload.update(image_message_id="IMG1", scene_path=str(scene), image_source="gemini", image_versions=1)
            db.set_payload(conn, draft_id, payload)
            db.set_status(conn, draft_id, "image_pending")
            notes = {"text": "Tiêu đề mới\nÝ mới", "last_id": "R1", "unreadable": False}
            with mock.patch.object(cli, "DATA", Path(tmp)), \
                    mock.patch.object(discord, "image_taps", return_value={"🔤"}), \
                    mock.patch.object(discord, "read_image_feedback", return_value=notes), \
                    mock.patch.object(cli, "build_image") as build, \
                    mock.patch.object(discord, "post_image_card", return_value="IMG2") as post, \
                    mock.patch.object(discord, "unreact"), mock.patch.object(discord, "reply"):
                cli.cmd_check_images(CFG, conn)
            build.assert_not_called()
            self.assertEqual(db.image_calls_for(conn, draft_id), 0)
            row = conn.execute("SELECT status, payload FROM drafts").fetchone()
            payload = json.loads(row["payload"])
            self.assertEqual(row["status"], "image_pending")
            self.assertEqual(payload["image_message_id"], "IMG2")
            self.assertEqual(payload["labels_override"], {"title": "Tiêu đề mới", "points": ["Ý mới"]})
            self.assertIn("không dùng AI", post.call_args.args[1]["content"])
            self.assertEqual(cli.labels_for(payload)["subtitle"], "Tóc khô")   # untouched parts stay


class UsageTest(unittest.TestCase):
    def test_calls_are_stored_per_draft_and_reported(self):
        usage._pending.clear()
        conn = db.connect(":memory:")
        reply = {"id": "gen-1", "model": "google/gemini-3.1-flash-image",
                 "usage": {"prompt_tokens": 1400, "completion_tokens": 1300, "cost": 0.068,
                           "completion_tokens_details": {"reasoning_tokens": 0}}}
        usage.record("image", reply, images_in=1)
        usage.record("fidelity", None, model="m", error="HTTP 500")
        usage.flush(conn, 45)
        rows = conn.execute("SELECT draft_id, stage, cost_usd, ok, generation_id FROM ai_calls ORDER BY stage").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(45, "fidelity", None, 0, ""), (45, "image", 0.068, 1, "gen-1")])
        report = usage.report(conn)
        self.assertIn("image", report)
        self.assertIn("#45", report)
        self.assertIn("$0.068", report)

    def test_requests_ask_openrouter_for_the_cost(self):
        from babebasket import scene

        sent = {}

        def post(url, **kw):
            sent.update(kw["body"])
            return {"choices": [{"message": {"content": "{}"}}]}

        scene._vision_json("q", [jpg()], {}, post, "k", stage="packaging")
        usage._pending.clear()
        self.assertEqual(sent["usage"], {"include": True})


class GalleryTest(unittest.TestCase):
    def test_parse_detail_keeps_every_photo(self):
        from babebasket.stores import dm

        detail = dm.parse_detail(json.loads(Path("tests/fixtures/dm_detail.json").read_text()))
        self.assertEqual(len(detail["gallery"]), 2)
        self.assertTrue(detail["gallery"][0]["thumb"].startswith("https://products.dm-static.com/"))
        self.assertEqual(detail["gallery"][0]["url"], detail["images"][0])

    def test_clean_gallery_drops_repeats_and_ai_photos(self):
        def noisy(color, shift=0):
            img = Image.new("RGB", (120, 120), "white")
            img.paste(color, (10 + shift, 10, 70 + shift, 110))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()

        thumbs = {"t1": noisy("red"), "t2": noisy("red", 1), "t3": noisy("blue", 40), "t4": noisy("green", 20)}
        items = [{"url": "u1", "thumb": "t1"}, {"url": "u1", "thumb": "t1"},          # same link
                 {"url": "u2", "thumb": "t2"},                                         # same picture, re-cropped
                 {"url": "u3", "thumb": "t3"}, {"url": "u4", "thumb": "t4", "ai": True},
                 {"url": "u5", "thumb": "broken"}]
        kept = cli.clean_gallery(items, fetch=lambda u: thumbs.get(u, b"not an image"))
        self.assertEqual(kept, ["u1", "u3", "u5"])

    def test_caption_card_shows_the_gallery_as_a_grid(self):
        product = {"store": "dm", "product_id": "1", "brand": "B", "name": "Lip Gloss", "image_url": "g1",
                   "product_url": "https://dm/p/1", "gallery": ["g1", "g2", "g3", "g4", "g5"]}
        quote = {"shelf_eur": 5.0, "coupon_pct": 0, "cost_eur": 5.0}
        card = discord.build_card(1, product, quote, {"captions": []}, [])
        images = [e["image"]["url"] for e in card["embeds"]]
        self.assertEqual(images, ["g1", "g2", "g3", "g4"])
        self.assertTrue(all(e["url"] == "https://dm/p/1" for e in card["embeds"]))

    def test_reply_anh_2_picks_that_gallery_photo(self):
        conn = db.connect(":memory:")
        draft_id = pending_draft(conn, pair=True)
        payload = json.loads(conn.execute("SELECT payload FROM drafts").fetchone()[0])
        payload["product"]["gallery"] = ["s-1", "s-2"]
        payload["product"]["pair"]["gallery"] = ["c-1", "c-2", "c-3"]
        db.set_payload(conn, draft_id, payload)
        self.assertEqual(cli.gallery_pick(conn, draft_id, "dùng ảnh 2 nha"), "s-2")
        self.assertEqual(cli.gallery_pick(conn, draft_id, "xả ảnh số 3"), "c-3")
        self.assertIsNone(cli.gallery_pick(conn, draft_id, "ảnh 9"))
        self.assertIsNone(cli.gallery_pick(conn, draft_id, "đẹp đó"))


if __name__ == "__main__":
    unittest.main()
