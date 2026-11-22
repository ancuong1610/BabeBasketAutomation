"""A shampoo + conditioner pair flows through draft, prompt, image and cards as one post."""

import io
import unittest
from unittest import mock

from PIL import Image

from babebasket import cli, discord, llm, postimage, scene

PAIR = {"store": "dm", "product_id": "c1", "brand": "syoss", "name": "Conditioner Intense Keratin, 250 ml",
        "price_eur": 3.45, "rating": 4.6, "review_count": 300, "image_url": "https://img/c1",
        "product_url": "https://www.dm.de/p/d/c1/"}
SHAMPOO = {"store": "dm", "product_id": "s1", "brand": "syoss", "name": "Shampoo Intense Keratin, 440 ml",
           "price_eur": 4.95, "rating": 4.8, "review_count": 900, "categories": "Shampoo",
           "image_url": "https://img/s1", "product_url": "https://www.dm.de/p/d/s1/", "pair": dict(PAIR)}
CFG = {"pricing": {"vnd_per_eur": 30000, "shipping_vnd_per_item": 0, "markup": 0.35, "round_to_thousand": 5},
       "coupons": {"dm": 0}, "llm": {"model": "m"}}
DRAFT = {"review_summary": "ok", "fit_for_vietnam": "ok",
         "labels": {"title": "Bộ gội xả Syoss", "points": ["Tóc mượt", "Bóng hơn"], "kind": "hair"},
         "captions": [{"style": "a", "text": "Bộ đôi gội xả nè"}] * 3}


def png(color, size):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


class PairFlowTest(unittest.TestCase):
    def test_draft_uses_both_products_and_adds_the_prices(self):
        calls = []
        with mock.patch.object(cli, "enrich", side_effect=lambda p: (calls.append(p["product_id"]) or ({}, [{"rating": 5}]))), \
                mock.patch.object(cli.time, "sleep"), \
                mock.patch.object(llm, "draft_post", return_value=dict(DRAFT)) as draft:
            payload = cli.make_draft(dict(SHAMPOO, pair=dict(PAIR)), CFG)
        self.assertEqual(calls, ["s1", "c1"])
        self.assertEqual(payload["quote"]["shelf_eur"], 8.4)
        self.assertEqual(draft.call_args.kwargs["pair"]["product_id"], "c1")

    def test_prompt_presents_the_set(self):
        user = llm.build_messages(SHAMPOO, {}, [], pair=PAIR, pair_detail={})[1]["content"]
        self.assertIn("BỘ ĐÔI", user)
        self.assertIn("Conditioner Intense Keratin", user)
        self.assertIn("Shampoo Intense Keratin", user)

    def test_single_product_prompt_has_no_pair_note(self):
        self.assertNotIn("BỘ ĐÔI", llm.build_messages(dict(SHAMPOO, pair=None), {}, [])[1]["content"])

    def test_caption_card_names_both(self):
        quote = {"shelf_eur": 8.4, "coupon_pct": 0, "cost_eur": 8.4}
        card = discord.build_card(1, SHAMPOO, quote, DRAFT, [])
        embed, twin = card["embeds"]
        self.assertIn("Shampoo Intense Keratin, 440 ml", embed["title"])
        names = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertIn("€8.40", names["Shelf price (gội + xả)"])
        self.assertIn("4.80", names["Rating gội"])
        # the conditioner has its own link and photo, so both products can be checked before ✅
        self.assertIn("Conditioner Intense Keratin", twin["title"])
        self.assertEqual(twin.get("url"), SHAMPOO["pair"].get("product_url"))
        self.assertEqual(twin["image"]["url"], SHAMPOO["pair"]["image_url"])
        self.assertNotEqual(twin.get("url"), embed.get("url"))
        self.assertIn("4.60", {f["name"]: f["value"] for f in twin["fields"]}["Rating xả"])

    def test_image_card_names_both(self):
        card = discord.build_image_card(1, SHAMPOO, "gemini", 2)
        self.assertIn("+ Conditioner Intense Keratin", card["embeds"][0]["title"])

    def test_both_packshots_go_into_the_image(self):
        sizes = []
        fetched = {"https://img/s1": png("red", (200, 400)), "https://img/c1": png("blue", (100, 300))}

        def gen(packshot, product, cfg_, **kw):
            sizes.append(Image.open(io.BytesIO(packshot)).size)
            return None
        payload = {"product": dict(SHAMPOO), "draft": DRAFT}
        with mock.patch.object(postimage, "save", side_effect=lambda img, out: out):
            cli.build_image(payload, "/tmp/x.jpg", {}, fetch_bytes=fetched.get, gen=gen)
        width, height = sizes[0]
        self.assertEqual(height, 400)
        self.assertGreater(width, 300)  # both bottles side by side

    def test_scene_prompt_asks_for_both(self):
        captured = {}

        def post(url, **kw):
            captured.update(kw)
            return {}
        scene.generate(png("red", (10, 10)), SHAMPOO, {"image": {"enabled": True}}, post=post, api_key="k")
        text = captured["body"]["messages"][0]["content"][0]["text"]
        self.assertIn("TWO products", text)
        self.assertIn("Conditioner Intense Keratin", text)


if __name__ == "__main__":
    unittest.main()
