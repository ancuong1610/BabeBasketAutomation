import io
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from babebasket import cli, discord

with (Path(__file__).resolve().parents[1] / "config.toml").open("rb") as file:
    PHOTO_CFG = {"photos": tomllib.load(file)["photos"]}


def photo(url, kind, this_shade=None):
    return {"url": url, "type": kind, "this_shade": this_shade}


class PhotoPlanTest(unittest.TestCase):
    def test_matches_first_rule_by_name_group_category_or_draft_kind(self):
        cases = [
            ({"name": "GNTM Make-up GESCHENKSET", "group": "makeup"}, None, "gift set", ["packshot", "contents"], 3),
            ({"name": "Pflege Set Rose"}, None, "gift set", ["packshot", "contents"], 3),
            ({"name": "x", "categories": "Kosmetik; Geschenksets"}, None, "gift set", ["packshot", "contents"], 3),
            ({"group": "makeup"}, "fragrance", "makeup",
             ["lip_swatch", "skin_swatch", "shade_range", "application", "texture"], 4),
            ({"categories": "Körperpflege; Bodylotion & Hautcreme"}, None, "cream", ["texture"], 2),
            ({}, "fragrance", "fragrance", ["packshot"], 2),
            ({}, "supplement", "supplement", ["label"], 2),
            ({"name": "Haarmaske Sunset Glow"}, None, "default", [], 1),
            ({"name": "Shampoo Intense Keratin, 440 ml"}, None, "default", [], 1),
            ({"name": "Pflege Set Rose", "pair": {"name": "Conditioner"}}, None, "pair", [], 1),
        ]
        for product, kind, rule, kinds, max_photos in cases:
            with self.subTest(product=product, kind=kind):
                payload = {"product": product, "draft": {"labels": {"kind": kind}}}
                self.assertEqual(cli.photo_plan(payload, PHOTO_CFG),
                                 {"rule": rule, "kinds": kinds, "max_photos": max_photos})

    def test_order_limit_and_confirmed_evidence_follow_the_classification(self):
        product = {"group": "makeup", "image_url": "hero", "gallery": ["hero", "extra"]}
        classify = mock.Mock(return_value=[photo("wrong", "texture", False), photo("skin", "skin_swatch", True),
                                           photo("range", "shade_range"), photo("unconfirmed", "lip_swatch"),
                                           photo("lip", "lip_swatch", True), photo("texture", "texture", True),
                                           photo("application", "application", True)])
        self.assertEqual(cli.plan_photos({"product": product}, PHOTO_CFG, classify=classify),
                         (["lip", "skin", "range"], {"texture": "texture", "swatch": "skin"}))
        classify.assert_called_once_with(product, PHOTO_CFG, cli.fetch_bytes)

    def test_rossmann_uses_original_packshot_without_a_gallery(self):
        product = {"store": "rossmann", "name": "Pflege Set Rose", "image_url": "replacement", "source_original": "box"}
        classify = mock.Mock()
        self.assertEqual(cli.plan_photos({"product": product}, PHOTO_CFG, classify=classify),
                         (["box"], {"texture": None, "swatch": None}))
        classify.assert_not_called()
        product.pop("source_original")
        self.assertEqual(cli.plan_photos({"product": product}, PHOTO_CFG, classify=classify)[0], ["replacement"])

    def test_contents_follow_packshot_and_label_is_used_for_a_supplement(self):
        classify = mock.Mock(return_value=[photo("label", "label"), photo("contents", "contents"),
                                           photo("another-contents", "contents")])
        product = {"name": "Let It Glow Geschenkset", "image_url": "hero", "gallery": ["hero", "extra"]}
        self.assertEqual(cli.plan_photos({"product": product}, PHOTO_CFG, classify=classify)[0], ["hero", "contents"])
        product["name"] = "Tabletten"
        payload = {"product": product, "draft": {"labels": {"kind": "supplement"}}}
        self.assertEqual(cli.plan_photos(payload, PHOTO_CFG, classify=classify)[0], ["label"])

    def test_evidence_is_collected_for_default_and_pair_plans_without_posting_it(self):
        product = {"name": "Shampoo", "image_url": "hero", "gallery": ["hero", "extra"]}
        classify = mock.Mock(return_value=[photo("unknown", "texture"), photo("actual", "texture", True)])
        for pair in (None, {"name": "Conditioner"}):
            with self.subTest(pair=pair):
                self.assertEqual(cli.plan_photos({"product": {**product, "pair": pair}}, PHOTO_CFG, classify=classify),
                                 ([], {"texture": "actual", "swatch": None}))

    def test_failed_classification_keeps_available_packshot(self):
        product = {"name": "Geschenkset", "image_url": "hero", "gallery": ["hero", "extra"]}
        self.assertEqual(cli.plan_photos({"product": product}, PHOTO_CFG, classify=mock.Mock(side_effect=OSError)),
                         (["hero"], {"texture": None, "swatch": None}))


def png_rgba() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 128)).save(buf, "PNG")
    return buf.getvalue()


class GiftSetPhotoTest(unittest.TestCase):
    def test_png_becomes_a_small_jpeg_and_bad_photos_are_skipped(self):
        def fetch(url):
            if "bad" in url:
                raise OSError("404")
            return png_rgba()
        out = cli.attachable_photos(["https://shop/box.png", "https://shop/bad.png"], fetch=fetch)
        self.assertEqual(len(out), 1)
        img = Image.open(io.BytesIO(out[0]))
        self.assertEqual((img.format, img.mode), ("JPEG", "RGB"))
        self.assertLessEqual(max(img.size), 1440)

    def test_image_card_uploads_and_shows_attached_photos(self):
        card = discord.build_image_card(1, {"name": "Set", "product_url": "https://p", "store": "rossmann"},
                                        "gemini", 1, supporting=["https://shop/box.png"], attached=1)
        self.assertEqual([a["filename"] for a in card["attachments"]], ["post.jpg", "support-1.jpg"])
        self.assertEqual(card["embeds"][1]["image"]["url"], "attachment://support-1.jpg")
        self.assertIn("Ảnh 1: AI · Ảnh 2: ảnh shop", card["content"])
        self.assertIn("Reply: thêm ảnh N / bỏ ảnh N / không ảnh phụ", card["content"])

    def test_card_photo_links_come_back_hero_first(self):
        msg = {"attachments": [{"filename": "support-2.jpg", "url": "u2"}, {"filename": "post.jpg", "url": "u0"},
                               {"filename": "support-1.jpg", "url": "u1"}]}
        with mock.patch.object(discord, "_call", return_value=msg):
            self.assertEqual(discord.card_image_urls("CH", "M"), ["u0", "u1", "u2"])

    def test_card_photo_links_are_read_from_embeds_too(self):
        msg = {"attachments": [], "embeds": [
            {"image": {"url": "https://cdn.discordapp.com/attachments/1/2/post.jpg?ex=1"}},
            {"image": {"url": "https://cdn.discordapp.com/attachments/1/3/support-1.jpg?ex=1"}},
            {"image": {"url": "https://shop/other.png"}}]}
        with mock.patch.object(discord, "_call", return_value=msg):
            self.assertEqual(discord.card_image_urls("CH", "M"),
                             ["https://cdn.discordapp.com/attachments/1/2/post.jpg?ex=1",
                              "https://cdn.discordapp.com/attachments/1/3/support-1.jpg?ex=1"])
