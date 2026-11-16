import unittest
from unittest import mock

from babebasket import db, discord, llm, pricing, shortlist
from babebasket.stores import rossmann

CFG = {
    "pricing": {"vnd_per_eur": 30000, "shipping_vnd_per_item": 0, "markup": 0.35, "round_to_thousand": 5},
    "coupons": {"dm": 0, "rossmann": 10},
    "shortlist": {"per_day": 3, "min_rating": 4.2, "min_reviews": 30, "repeat_after_days": 45,
                  "preferred_brands": ["Balea"]},
}


def product(pid, brand="Balea", rating=4.6, reviews=500, price=2.95, store="dm"):
    return {"store": store, "product_id": pid, "gtin": "", "brand": brand, "name": f"Produkt {pid}",
            "price_eur": price, "unit_price": "", "rating": rating, "review_count": reviews,
            "categories": "", "image_url": "https://img", "product_url": "https://www.dm.de/p/d/1/"}


class PricingTest(unittest.TestCase):
    def test_quote_rounds_up_to_step(self):
        q = pricing.quote(2.75, "dm", CFG)
        # 2.75 * 30000 = 82,500; * 1.35 = 111,375 -> 115🐠
        self.assertEqual(q["sale_k"], 115)
        self.assertEqual(q["price_text"], "115🐠")
        self.assertEqual(q["cost_eur"], 2.75)

    def test_store_coupon_lowers_cost(self):
        q = pricing.quote(10.0, "rossmann", CFG)
        self.assertEqual(q["cost_eur"], 9.0)
        self.assertEqual(q["coupon_pct"], 10)


class ShortlistTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_filters_ranks_and_limits_brand_repeats(self):
        db.upsert_products(self.conn, [
            product("1", "Balea", 4.8, 2000),
            product("2", "Balea", 4.7, 1500),
            product("3", "Balea", 4.7, 1400),   # third Balea: dropped for variety
            product("4", "Garnier", 4.5, 800),
            product("5", "Nivea", 4.9, 10),     # too few reviews
            product("6", "Dove", 3.9, 900),     # rating too low
        ])
        picks = [p["product_id"] for p in shortlist.pick(self.conn, CFG)]
        self.assertEqual(picks, ["1", "2", "4"])

    def test_skips_recently_drafted(self):
        db.upsert_products(self.conn, [product("1"), product("2", "Garnier")])
        db.add_draft(self.conn, "dm", "1", {})
        self.assertEqual([p["product_id"] for p in shortlist.pick(self.conn, CFG)], ["2"])

    def test_many_reviews_beat_few_perfect_ones(self):
        self.assertGreater(shortlist.score(4.6, 3000, "", []), shortlist.score(5.0, 31, "", []))


GOOD_DRAFT = {
    "review_summary": "Review khen thơm.",
    "fit_for_vietnam": "Hợp tóc khô.",
    "labels": {"benefits": ["Dưỡng tóc khô", "Tóc mượt hơn"], "who_for": "Dành cho tóc khô"},
    "captions": [
        {"style": "everyday story", "text": "Các babe ơi {PRICE}/tuýp 200ml nha 🥰"},
        {"style": "curiosity hook", "text": "Ủa sao thơm vậy nè {PRICE}/tuýp 200ml"},
        {"style": "engagement question", "text": "Các bà ủ tóc mấy lần một tuần nè?"},
        {"style": "direct sales", "text": "Order sốp nha {PRICE}/tuýp 200ml"},
    ],
}


class LlmOutputTest(unittest.TestCase):
    def test_good_draft_passes(self):
        self.assertEqual(llm.validate(GOOD_DRAFT), [])
        self.assertEqual(llm.warnings(GOOD_DRAFT), [])

    def test_forbidden_word_and_llm_prices_are_caught(self):
        bad = {**GOOD_DRAFT, "captions": [{"style": "a", "text": "Tiết kiệm tiền nha"},
                                          {"style": "b", "text": "Chỉ 120k thôi"},
                                          {"style": "c", "text": "Giá 2,75 €"}]}
        problems = llm.validate(bad)
        self.assertIn("caption 1 uses 'tiền'", problems)
        self.assertIn("caption 2 writes a price itself", problems)
        self.assertIn("caption 3 writes a price itself", problems)

    def test_fill_price(self):
        import copy

        filled = llm.fill_price(copy.deepcopy(GOOD_DRAFT), "115🐠")
        self.assertIn("115🐠/tuýp 200ml", filled["captions"][0]["text"])

    def test_parse_json_reply_with_code_fence(self):
        self.assertEqual(llm.parse_json_reply('Here:\n```json\n{"a": 1}\n```'), {"a": 1})

    def test_supplement_note_added(self):
        p = product("1")
        msgs = llm.build_messages(p, {"breadcrumbs": ["Gesundheit", "Nahrungsergänzungsmittel"]}, [])
        self.assertIn("thực phẩm chức năng", msgs[1]["content"])
        self.assertIn("{PRICE}", msgs[0]["content"])


class DiscordTest(unittest.TestCase):
    def test_card_shape(self):
        q = pricing.quote(2.75, "dm", CFG)
        card = discord.build_card(7, product("1"), q, GOOD_DRAFT, ["caption 2 has no price"])
        embed = card["embeds"][0]
        self.assertIn("Draft #7", card["content"])
        self.assertIn("1️⃣", embed["description"])
        self.assertLessEqual(len(embed["description"]), 4096)
        self.assertEqual(embed["image"]["url"], "https://img")
        self.assertTrue(any(f["name"] == "⚠️ Check" for f in embed["fields"]))
        self.assertTrue(all(v is not None for v in embed.values()))

    def _decide(self, reactions, owners=()):
        def fake(channel, message, emoji):
            return reactions.get(emoji, [{"id": "bot", "bot": True}])
        counts = {"reactions": [{"emoji": {"name": e}, "count": 1 + len(u)} for e, u in reactions.items()]}
        with mock.patch.object(discord, "reactors", side_effect=fake), \
                mock.patch.object(discord, "_call", return_value=counts):
            return discord.read_decision("c", "m", 4, list(owners))

    def test_undecided_when_only_bot_reacted(self):
        self.assertIsNone(self._decide({}))

    def test_approve_with_caption(self):
        owner = [{"id": "42"}]
        self.assertEqual(self._decide({"✅": owner, "3️⃣": owner}, ["42"])["caption"], 3)

    def test_approve_without_number_defaults_to_first(self):
        self.assertEqual(self._decide({"✅": [{"id": "42"}]})["caption"], 1)

    def test_reject_wins(self):
        owner = [{"id": "42"}]
        self.assertEqual(self._decide({"✅": owner, "❌": owner})["status"], "rejected")

    def test_other_users_ignored_when_owner_set(self):
        self.assertIsNone(self._decide({"✅": [{"id": "99"}]}, ["42"]))


class RossmannTest(unittest.TestCase):
    def test_robots_rules(self):
        self.assertTrue(rossmann.allowed("https://www.rossmann.de/de/pflege-und-duft/haare/c/olcat2_1"))
        self.assertTrue(rossmann.allowed("https://www.rossmann.de/de/pflege-und-duft/haare/c/olcat2_1?pageIndex=2"))
        self.assertFalse(rossmann.allowed("https://www.rossmann.de/de/search/?text=balea"))
        self.assertFalse(rossmann.allowed("https://www.rossmann.de/de/haare/c/x?sort=price"))
        self.assertFalse(rossmann.allowed("https://example.com/de/x"))

    def test_parse_json_ld_item_list(self):
        page = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"ItemList",
        "itemListElement":[{"@type":"ListItem","item":{"@type":"Product","name":"Haarkur","sku":"123",
        "brand":{"@type":"Brand","name":"Isana"},"image":["https://img/1.jpg"],"url":"/de/p/123",
        "offers":{"@type":"Offer","price":"1.95","priceCurrency":"EUR"},
        "aggregateRating":{"ratingValue":"4.5","reviewCount":"88"}}}]}</script>"""
        rows = rossmann.parse_page(page)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["brand"], r["price_eur"], r["rating"], r["review_count"]), ("Isana", 1.95, 4.5, 88))
        self.assertEqual(r["product_url"], "https://www.rossmann.de/de/p/123")


if __name__ == "__main__":
    unittest.main()
