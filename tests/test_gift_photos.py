import io
import unittest
from unittest import mock

from PIL import Image

from babebasket import cli, discord


def png_rgba() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 128)).save(buf, "PNG")
    return buf.getvalue()


class GiftSetPhotoTest(unittest.TestCase):
    def test_gift_sets_are_recognised(self):
        self.assertTrue(cli.is_gift_set({"name": "GNTM Glowing Skin Essentials Make-up Geschenkset, 1 St"}))
        self.assertTrue(cli.is_gift_set({"name": "Pflege Set Rose"}))
        self.assertTrue(cli.is_gift_set({"name": "x", "categories": "Geschenksets"}))
        self.assertFalse(cli.is_gift_set({"name": "Shampoo Intense Keratin, 440 ml"}))
        self.assertFalse(cli.is_gift_set({"name": "Haarmaske Sunset Glow"}))

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
