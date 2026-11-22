"""Makeup posts: the AI image plus the shop's swatch/shade photos, posted to Threads as a carousel."""

import io
import json
import unittest
from unittest import mock

from PIL import Image

from babebasket import catalog, cli, db, discord, scene, threads
from tests.test_gift_photos import PHOTO_CFG, photo

G = "https://products.dm-static.com/images/f_auto,q_auto,c_fit,h_1200,w_1200/v1/assets/pas/images/{}/x"
LIPGLOSS = {"store": "dm", "product_id": "L1", "brand": "Maybelline New York", "group": "makeup",
            "name": "Lipgloss Lifter Gloss 005 Petal, 5,4 ml", "product_url": "https://dm/p/L1",
            "image_url": G.format("a"), "gallery": [G.format(c) for c in "abcdef"]}


def jpg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buf, "JPEG")
    return buf.getvalue()


def build(payload, out, cfg, **kw):
    payload["image_path"], payload["image_source"] = str(out), "gemini"
    return str(out)


class SelectionTest(unittest.TestCase):
    def test_lip_makeup_is_its_own_group_lip_balm_is_not(self):
        self.assertEqual(catalog.classify({"name": "Lipgloss Lifter Gloss 005 Petal, 5,4 ml"})[0], "makeup")
        self.assertEqual(catalog.classify({"name": "Lippenstift Matte Ink 20, 5 ml"})[0], "makeup")
        self.assertEqual(catalog.classify({"name": "Lippenpflege Original, 4,8 g"})[0], "extra")

    def test_shade_from_name(self):
        self.assertEqual(scene.shade_of("Lipgloss Lifter Gloss 005 Petal, 5,4 ml"), "005 Petal")
        self.assertEqual(scene.shade_of("Shampoo Repair, 250 ml"), "")

    def test_one_vision_call_with_small_jpegs_returns_labels_in_gallery_order(self):
        fetched, sent = [], []

        def post(url, **kw):
            sent.append(kw["body"]["messages"][0]["content"])
            labels = [{"i": 3, "type": "label"}, {"i": 9, "type": "lip_swatch"}, {"i": "x"},
                      {"i": 2, "type": "lip_swatch", "this_shade": False}, {"i": 1, "type": "contents"}]
            return {"choices": [{"message": {"content": json.dumps({"photos": labels})}}]}

        out = scene.classify_gallery(LIPGLOSS, {}, lambda u: fetched.append(u) or jpg(), post, "k")
        from babebasket import usage
        self.assertEqual(usage._pending[-1]["stage"], "gallery")
        usage._pending.clear()
        (parts,) = sent
        self.assertEqual(len(parts) - 1, 5)  # photos 2-6, not the packshot
        self.assertIn("dosage panel", parts[0]["text"])
        self.assertTrue(all("f_jpg,q_auto,c_fit,h_400,w_400" in u for u in fetched))
        self.assertEqual(out, [photo(scene.small_jpg(G.format("b"), 1200), "contents"),
                               photo(scene.small_jpg(G.format("c"), 1200), "lip_swatch", False),
                               photo(scene.small_jpg(G.format("d"), 1200), "label")])

    def test_without_gallery_skips_fetch_and_vision(self):
        fetch, post = mock.Mock(), mock.Mock()
        self.assertEqual(scene.classify_gallery({"store": "rossmann"}, {}, fetch, post), [])
        fetch.assert_not_called()
        post.assert_not_called()


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        payload = {"product": dict(LIPGLOSS), "quote": {}, "warnings": [],
                   "draft": {"captions": [{"style": "a", "text": "Son xinh"}]}}
        self.draft_id = db.add_draft(self.conn, "dm", "L1", payload)
        db.set_status(self.conn, self.draft_id, "approved", discord_message_id="CAP", chosen_caption=1)
        self.cfg = {**PHOTO_CFG, "discord": {"channel_id": "CH", "owner_user_ids": []},
                    "image": {"enabled": True, "max_per_day": 4, "max_per_post": 2}, "threads": {"enabled": True}}

    def payload(self):
        return json.loads(self.conn.execute("SELECT payload FROM drafts").fetchone()[0])

    def test_image_card_shows_the_photos_that_will_be_posted(self):
        with mock.patch.object(cli, "build_image", side_effect=build), \
                mock.patch.object(scene, "classify_gallery", return_value=[photo("s1", "lip_swatch", True),
                                                                            photo("s2", "skin_swatch", True)]) as classify, \
                mock.patch.object(cli, "fetch_bytes", return_value=jpg()), \
                mock.patch.object(discord, "post_image_card", return_value="IMG1") as post, \
                mock.patch.object(discord, "reply"), mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
        classify.assert_called_once()
        card = post.call_args.args[1]
        self.assertIn("Ảnh 1: AI · Ảnh 2–3: ảnh shop", card["content"])
        self.assertEqual([e["image"]["url"] for e in card["embeds"]],
                         ["attachment://post.jpg", "attachment://support-1.jpg", "attachment://support-2.jpg"])
        self.assertEqual({e["url"] for e in card["embeds"]}, {"https://dm/p/L1"})
        self.assertEqual(self.payload()["supporting"], ["s1", "s2"])
        self.assertEqual(self.payload()["supporting_attached"], 2)

    def test_cream_category_gets_one_texture_photo(self):
        payload = self.payload()
        payload["product"].update(group="body", name="Bodylotion", categories="Körperpflege; Bodylotion & Hautcreme")
        db.set_payload(self.conn, self.draft_id, payload)
        with mock.patch.object(cli, "build_image", side_effect=build), \
                mock.patch.object(scene, "classify_gallery", return_value=[photo("detail", "product_detail"),
                                                                            photo("texture.jpg", "texture", True),
                                                                            photo("texture2.jpg", "texture", True)]) as classify, \
                mock.patch.object(cli, "fetch_bytes", return_value=jpg()), \
                mock.patch.object(discord, "post_image_card", return_value="IMG1") as post, \
                mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
        classify.assert_called_once()
        self.assertEqual(self.payload()["photo_rule"], "cream")
        self.assertEqual(self.payload()["supporting"], ["texture.jpg"])
        self.assertIn("Ảnh 2: ảnh shop", post.call_args.args[1]["content"])

    def test_shampoo_gets_evidence_before_image_but_no_supporting_photos(self):
        payload = self.payload()
        payload["product"].update(group="hair", name="Shampoo", categories="Shampoo")
        db.set_payload(self.conn, self.draft_id, payload)
        def first_image(payload, out, cfg, **kwargs):
            self.assertEqual(payload["evidence"], {"texture": "texture.jpg", "swatch": None})
            return build(payload, out, cfg, **kwargs)

        with mock.patch.object(cli, "build_image", side_effect=first_image), \
                mock.patch.object(scene, "classify_gallery", return_value=[photo("texture.jpg", "texture", True)]) as classify, \
                mock.patch.object(discord, "post_image_card", return_value="IMG1"), \
                mock.patch.object(discord, "reply"), mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
        classify.assert_called_once()
        self.assertEqual(self.payload()["supporting"], [])

    def test_gallery_is_classified_once_when_the_first_packshot_download_fails(self):
        labels = [photo("lip", "lip_swatch", True)]
        with mock.patch.object(cli, "build_image", return_value=None) as render, \
                mock.patch.object(scene, "classify_gallery", return_value=labels) as classify, \
                mock.patch.object(cli, "fetch_bytes", return_value=jpg()), \
                mock.patch.object(discord, "post_image_card", return_value="IMG1"), mock.patch.object(discord, "unreact"):
            cli.cmd_images(self.cfg, self.conn)
            self.assertEqual(self.payload()["photo_rule"], "makeup")
            render.side_effect = build
            cli.cmd_images(self.cfg, self.conn)
        classify.assert_called_once()
        self.assertEqual(render.call_count, 2)
        self.assertEqual(self.payload()["evidence"], {"texture": None, "swatch": "lip"})
        self.assertEqual(self.payload()["image_message_id"], "IMG1")

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
