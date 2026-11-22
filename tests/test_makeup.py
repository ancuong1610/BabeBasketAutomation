"""Makeup posts: the AI image plus the shop's swatch/shade photos, posted to Threads as a carousel."""

import io
import json
import unittest
from unittest import mock

from PIL import Image

from babebasket import catalog, cli, db, discord, scene, threads

G = "https://products.dm-static.com/images/f_auto,q_auto,c_fit,h_1200,w_1200/v1/assets/pas/images/{}/x"
LIPGLOSS = {"store": "dm", "product_id": "L1", "brand": "Maybelline New York", "group": "makeup",
            "name": "Lipgloss Lifter Gloss 005 Petal, 5,4 ml", "product_url": "https://dm/p/L1",
            "image_url": G.format("a"), "gallery": [G.format(c) for c in "abcdef"]}


def jpg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buf, "JPEG")
    return buf.getvalue()


class SelectionTest(unittest.TestCase):
    def test_lip_makeup_is_its_own_group_lip_balm_is_not(self):
        self.assertEqual(catalog.classify({"name": "Lipgloss Lifter Gloss 005 Petal, 5,4 ml"})[0], "makeup")
        self.assertEqual(catalog.classify({"name": "Lippenstift Matte Ink 20, 5 ml"})[0], "makeup")
        self.assertEqual(catalog.classify({"name": "Lippenpflege Original, 4,8 g"})[0], "extra")

    def test_shade_from_name(self):
        self.assertEqual(scene.shade_of("Lipgloss Lifter Gloss 005 Petal, 5,4 ml"), "005 Petal")
        self.assertEqual(scene.shade_of("Shampoo Repair, 250 ml"), "")

    def test_swatches_of_this_shade_first_never_another_shade(self):
        photos = ["p2", "p3", "p4", "p5", "p6"]
        labels = [{"i": 1, "type": "shade_range"}, {"i": 2, "type": "lip_swatch", "this_shade": False},
                  {"i": 3, "type": "skin_swatch", "this_shade": True}, {"i": 4, "type": "lip_swatch", "this_shade": True},
                  {"i": 5, "type": "product_detail"}]
        self.assertEqual(scene.pick_supporting(labels, photos), ["p5", "p4", "p2"])
        self.assertEqual(scene.pick_supporting([{"i": 9, "type": "lip_swatch"}, {"i": "x"}], photos), [])

    def test_one_vision_call_with_small_jpegs_returns_large_jpegs(self):
        fetched, sent = [], {}

        def post(url, **kw):
            sent["images"] = len(kw["body"]["messages"][0]["content"]) - 1
            return {"choices": [{"message": {"content": json.dumps(
                {"photos": [{"i": 2, "type": "lip_swatch", "this_shade": True}]})}}]}

        out = scene.select_supporting(LIPGLOSS, {}, lambda u: fetched.append(u) or jpg(), post, "k")
        from babebasket import usage
        self.assertEqual(usage._pending[-1]["stage"], "gallery")
        usage._pending.clear()
        self.assertEqual(sent["images"], 5)                       # photos 2-6, not the packshot
        self.assertTrue(all("f_jpg,q_auto,c_fit,h_400,w_400" in u for u in fetched))
        self.assertEqual(out, [G.format("c").replace("f_auto", "f_jpg")])


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        payload = {"product": dict(LIPGLOSS), "quote": {}, "warnings": [],
                   "draft": {"captions": [{"style": "a", "text": "Son xinh"}]}}
        self.draft_id = db.add_draft(self.conn, "dm", "L1", payload)
        db.set_status(self.conn, self.draft_id, "approved", discord_message_id="CAP", chosen_caption=1)
        self.cfg = {"discord": {"channel_id": "CH", "owner_user_ids": []},
                    "image": {"enabled": True, "max_per_day": 4, "max_per_post": 2}, "threads": {"enabled": True}}

    def payload(self):
        return json.loads(self.conn.execute("SELECT payload FROM drafts").fetchone()[0])

    def test_image_card_shows_the_photos_that_will_be_posted(self):
        def build(payload, out, cfg, ai=True, **kw):
            payload["image_path"], payload["image_source"] = str(out), "gemini"
            return str(out)

        with mock.patch.object(cli, "build_image", side_effect=build), \
                mock.patch.object(cli, "supporting_photos", return_value=["s1", "s2"]) as pick, \
                mock.patch.object(discord, "post_image_card", return_value="IMG1") as post, \
                mock.patch.object(discord, "reply"), mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
        pick.assert_called_once()
        card = post.call_args.args[1]
        self.assertIn("Đăng kèm 2 ảnh", card["content"])
        self.assertEqual([e["image"]["url"] for e in card["embeds"]], ["attachment://post.jpg", "s1", "s2"])
        self.assertEqual({e["url"] for e in card["embeds"]}, {"https://dm/p/L1"})
        self.assertEqual(self.payload()["supporting"], ["s1", "s2"])

    def test_non_makeup_gets_no_supporting_photos(self):
        payload = self.payload()
        payload["product"]["group"] = "hair"
        db.set_payload(self.conn, self.draft_id, payload)
        with mock.patch.object(cli, "build_image", return_value="x.jpg"), \
                mock.patch.object(cli, "supporting_photos") as pick, \
                mock.patch.object(discord, "post_image_card", return_value="IMG1"), \
                mock.patch.object(discord, "reply"), mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
        pick.assert_not_called()

    def test_reply_khong_anh_phu_drops_them(self):
        payload = self.payload()
        payload.update(supporting=["s1"], image_message_id="IMG1")
        db.set_payload(self.conn, self.draft_id, payload)
        db.set_status(self.conn, self.draft_id, "image_pending")
        notes = {"text": "không ảnh phụ nha", "last_id": "R1", "unreadable": False}
        with mock.patch.object(discord, "image_taps", return_value=set()), \
                mock.patch.object(discord, "read_image_feedback", return_value=notes), \
                mock.patch.object(cli, "send_image_card") as resend, mock.patch.object(discord, "reply"):
            cli.cmd_check_images(self.cfg, self.conn)
        resend.assert_not_called()                       # no paid AI edit for this
        self.assertEqual(self.payload()["supporting"], [])

    def test_publish_sends_the_carousel(self):
        payload = self.payload()
        payload.update(supporting=["s1", "s2"], image_message_id="IMG1")
        db.set_payload(self.conn, self.draft_id, payload)
        db.set_status(self.conn, self.draft_id, "ready")
        with mock.patch.object(discord, "card_image_url", return_value="https://cdn/post.jpg"), \
                mock.patch.object(threads, "maybe_refresh", return_value=False), \
                mock.patch.object(threads, "publish", return_value={"id": "M", "permalink": "L"}) as pub, \
                mock.patch.object(discord, "reply"):
            cli.cmd_publish(self.cfg, self.conn)
        pub.assert_called_once_with("Son xinh", "https://cdn/post.jpg", extra_images=["s1", "s2"])


class CarouselApiTest(unittest.TestCase):
    def test_children_then_carousel_then_publish(self):
        calls = []

        def api(url, method="GET", **kw):
            path, _, query = url.partition("?")
            calls.append((path, query))
            if path == "me":
                return {"id": "U1"}
            if path == "U1/threads":
                return {"id": f"C{len(calls)}"}
            if path == "U1/threads_publish":
                return {"id": "M1"}
            if path == "M1":
                return {"permalink": "L"}
            return {"status": "FINISHED"}

        with mock.patch.object(threads, "request_json", side_effect=api), \
                mock.patch.object(threads, "_url", side_effect=lambda p, **q: p + "?" + "&".join(
                    f"{k}={v}" for k, v in q.items() if v is not None)), \
                mock.patch.object(threads.time, "sleep"):
            out = threads.publish("Son xinh", "hero", extra_images=["s1", "s2"])
        creates = [q for p, q in calls if p == "U1/threads"]
        self.assertEqual(len(creates), 4)
        self.assertIn("image_url=hero", creates[0])
        self.assertIn("is_carousel_item=true", creates[0])
        self.assertIn("media_type=CAROUSEL", creates[3])
        self.assertIn("text=Son xinh", creates[3])
        self.assertEqual(sum(p == "U1/threads_publish" for p, _ in calls), 1)
        self.assertEqual(out["id"], "M1")


if __name__ == "__main__":
    unittest.main()
