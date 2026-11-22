"""Owner controls that cost nothing: 🖼️ new product photo before ✅, 🔤 new words on the image; and cost tracking."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from babebasket import cli, db, discord, postimage, usage

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

    def wait_for_photo(self, draft_id, prompt):
        payload = self.payload(draft_id)
        db.set_payload(self.conn, draft_id, {**payload, "photo_prompt": prompt, "photo_wait_since": db.now()})

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

    def test_plain_photo_goes_to_the_only_waiting_draft_and_clears_the_wait(self):
        draft_id = pending_draft(self.conn)
        pending_draft(self.conn, card="101")
        self.wait_for_photo(draft_id, "110")
        messages = [
            {"id": "109", "author": {"id": "7"}, "content": "https://shop/earlier.jpg"},
            {"id": "200", "author": {"id": "7"}, "attachments": [{"filename": "new.jpg", "url": "https://cdn/new.jpg"}]},
            {"id": "201", "author": {"id": "7"}, "content": "https://shop/later.jpg"},
        ]
        self.run_replies(messages, {"https://cdn/new.jpg": jpg()})
        payload = self.payload(draft_id)
        self.assertEqual(payload["product"]["source_replaced_from"], "https://cdn/new.jpg")
        self.assertTrue(payload["photo_received"])
        self.assertEqual(len(self.replies), 1)

    def test_plain_gallery_number_and_prompt_reply_use_product_gallery(self):
        for text, reference in [("2", {}), ("ảnh 2", {"message_id": "110"}),
                                ("số 2 xả", {"message_id": "110"})]:
            with self.subTest(text=text):
                draft_id = pending_draft(self.conn, pair=True)
                self.wait_for_photo(draft_id, "110")
                payload = self.payload(draft_id)
                payload["product"]["gallery"] = ["https://shop/s1.jpg", "https://shop/s2.jpg"]
                payload["product"]["pair"]["gallery"] = ["https://shop/c1.jpg", "https://shop/c2.jpg"]
                db.set_payload(self.conn, draft_id, payload)
                self.run_replies([{"id": "200", "author": {"id": "7"}, "content": text,
                                   "message_reference": reference}],
                                 {"https://shop/s2.jpg": jpg(), "https://shop/c2.jpg": jpg()})
                payload = self.payload(draft_id)
                self.assertTrue(payload["photo_received"])
                key = payload["product"]["pair"] if "xả" in text else payload["product"]
                self.assertEqual(key["source_replaced_from"], key["gallery"][1])
                db.set_status(self.conn, draft_id, "approved")

    def test_two_waiting_drafts_get_one_reply_under_the_ambiguous_message(self):
        first = pending_draft(self.conn)
        second = pending_draft(self.conn, card="101")
        self.wait_for_photo(first, "110")
        self.wait_for_photo(second, "111")
        message = {"id": "200", "author": {"id": "7"}, "content": "https://shop/ambiguous.jpg"}
        with mock.patch.object(discord, "replies_after", side_effect=[[message], []]) as read, \
                mock.patch.object(discord, "reply") as reply, mock.patch.object(cli, "fetch_bytes") as fetch:
            cli.cmd_source_replies(CFG, self.conn)
            cli.cmd_source_replies(CFG, self.conn)
        fetch.assert_not_called()
        self.assertEqual(read.call_args.args[1], "200")
        reply.assert_called_once()
        self.assertEqual(reply.call_args.args[:2], ("CH", "200"))
        self.assertIn("Reply", reply.call_args.args[2])
        for draft_id in (first, second):
            self.assertNotIn("photo_received", self.payload(draft_id))

    def test_bad_prompted_attachment_keeps_the_wait(self):
        draft_id = pending_draft(self.conn)
        self.wait_for_photo(draft_id, "110")
        message = {"id": "200", "author": {"id": "7"}, "message_reference": {"message_id": "110"},
                   "attachments": [{"url": "https://cdn/new.pdf", "filename": "new.pdf"}]}
        self.run_replies([message], {"https://cdn/new.pdf": b"%PDF-data"})
        self.assertIn("không phải file ảnh", self.replies[0])
        self.assertNotIn("photo_received", self.payload(draft_id))


class PhotoPromptTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.draft = pending_draft(self.conn, pair=True)
        self.enterContext(mock.patch.object(cli.time, "sleep"))
        self.taps = self.enterContext(mock.patch.object(discord, "owner_taps", return_value={discord.PHOTO}))
        self.api = self.enterContext(mock.patch.object(discord, "_call", return_value={"id": "110"}))
        self.clock = self.enterContext(mock.patch.object(db, "now", return_value="2026-11-22T08:00:00+00:00"))

    def row(self):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (self.draft,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def test_photo_prompt_replies_once_and_saves_the_message_and_time(self):
        cli.cmd_check(CFG, self.conn)
        cli.cmd_check(CFG, self.conn)
        self.api.assert_called_once()
        message = self.api.call_args.args[2]
        self.assertEqual(message["message_reference"], {"message_id": "100"})
        for text in ("không cần bấm Reply", "đính kèm ảnh", "dán link ảnh", "lưới ảnh", 'ghi thêm chữ "xả"', "✅"):
            self.assertIn(text, message["content"])
        status, payload = self.row()
        self.assertEqual(status, "pending")
        self.assertEqual(payload["photo_prompt"], "110")
        self.assertEqual(payload["photo_wait_since"], "2026-11-22T08:00:00+00:00")

    def test_approval_is_held_before_ten_minutes_and_released_at_ten(self):
        self.taps.return_value.add(discord.APPROVE)
        cli.cmd_check(CFG, self.conn)
        self.assertEqual(self.row()[0], "pending")
        self.clock.return_value = "2026-11-22T08:09:59+00:00"
        cli.cmd_check(CFG, self.conn)
        self.assertEqual(self.row()[0], "pending")
        self.api.assert_called_once()
        self.clock.return_value = "2026-11-22T08:10:00+00:00"
        cli.cmd_check(CFG, self.conn)
        self.assertEqual(self.row()[0], "approved")

    def test_receiving_photo_releases_approval_immediately(self):
        cli.cmd_check(CFG, self.conn)
        _, payload = self.row()
        db.set_payload(self.conn, self.draft, {**payload, "photo_received": True})
        self.taps.return_value.add(discord.APPROVE)
        cli.cmd_check(CFG, self.conn)
        self.assertEqual(self.row()[0], "approved")


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
            notes = {"text": "Tiêu đề mới\nÝ mới", "last_id": "R1", "unreadable": False,
                     "messages": [{"content": "Tiêu đề mới\nÝ mới"}]}
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

    def test_no_text_style_rejects_relettering_on_the_image_card(self):
        conn = db.connect(":memory:")
        draft_id = pending_draft(conn)
        payload = json.loads(conn.execute("SELECT payload FROM drafts").fetchone()[0])
        payload.update(image_message_id="IMG1", image_source="gemini", style="k4")
        db.set_payload(conn, draft_id, payload)
        db.set_status(conn, draft_id, "image_pending")

        for text in ("", "Tiêu đề mới"):
            with self.subTest(text=text):
                notes = {"text": text, "last_id": "R1" if text else None, "unreadable": False,
                         "messages": [{"content": text}] if text else []}
                with mock.patch.object(discord, "image_taps", return_value={"🔤"}), \
                        mock.patch.object(discord, "read_image_feedback", return_value=notes), \
                        mock.patch.object(discord, "post_image_card") as post, \
                        mock.patch.object(postimage, "render") as render, \
                        mock.patch.object(discord, "reply") as reply:
                    cli.cmd_check_images(CFG, conn)
                    self.assertIsNone(cli.reletter(payload, "unused.jpg"))
                reply.assert_called_once_with("CH", "IMG1", discord.TEXT_STYLE_NOTICE)
                render.assert_not_called()
                post.assert_not_called()

        saved = json.loads(conn.execute("SELECT payload FROM drafts").fetchone()[0])
        self.assertNotIn("labels_override", saved)
        self.assertEqual(saved["image_message_id"], "IMG1")


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
