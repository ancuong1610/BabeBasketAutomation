import json
import unittest
from unittest import mock

from babebasket import db, discord, llm, pricing, shortlist
from babebasket.config import ROOT
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


GROUPS = {"hair": {"per_day": 3}, "body": {"per_day": 2},
          "extra": {"per_day": 1, "min_rating": 4.5, "min_reviews": 100}}
SHOP = {**CFG, "groups": GROUPS,
        "shortlist": {**CFG["shortlist"], "per_day": 6, "sweet_spot_eur": 10, "max_price_eur": 20,
                      "max_price_gift_set_eur": 40}}


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


def safe(_product):
    return True, ""


def item(pid, brand, name, cats, rating=4.6, reviews=500, price=3.95, store="dm"):
    p = product(pid, brand, rating, reviews, price, store)
    p.update(name=name, categories=cats)
    return p


SHAMPOO = "Shampoo"


class ShortlistTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def pick(self, cfg=CFG, check=safe, **kw):
        return [p["product_id"] for p in shortlist.pick(self.conn, cfg, check=check, **kw)]

    def test_filters_ranks_and_limits_brand_repeats(self):
        cfg = {**CFG, "groups": {"hair": {"per_day": 3}}}
        db.upsert_products(self.conn, [
            item("1", "Balea", "Haarkur A", SHAMPOO, 4.8, 2000),
            item("2", "Balea", "Haarkur B", SHAMPOO, 4.7, 1500),
            item("3", "Balea", "Haarkur C", SHAMPOO, 4.7, 1400),   # third Balea: dropped for variety
            item("4", "Garnier", "Haarkur D", SHAMPOO, 4.5, 800),
            item("5", "Nivea", "Haarkur E", SHAMPOO, 4.9, 10),     # too few reviews
            item("6", "Dove", "Haarkur F", SHAMPOO, 3.9, 900),     # rating too low
        ])
        self.assertEqual(self.pick(cfg), ["1", "2", "4"])

    def test_skips_recently_drafted(self):
        db.upsert_products(self.conn, [item("1", "Balea", "Haarkur", SHAMPOO), item("2", "Garnier", "Haarkur", SHAMPOO)])
        db.add_draft(self.conn, "dm", "1", {})
        self.assertEqual(self.pick(), ["2"])

    def test_daily_quota_per_group(self):
        brands = iter(f"B{i}" for i in range(100))
        rows = [item(f"h{i}", next(brands), f"Haarkur {i}", "Haarkur & Haarmaske", 4.8, 900 - i) for i in range(5)]
        rows += [item(f"b{i}", next(brands), f"Bodylotion {i}", "Bodylotion & Hautcreme", 4.7, 800 - i) for i in range(5)]
        rows += [item(f"e{i}", next(brands), f"Tagescreme {i}", "Gesichtspflege; Tagescreme", 4.8, 700 - i) for i in range(5)]
        db.upsert_products(self.conn, rows)
        self.assertEqual(self.pick(SHOP), ["h0", "h1", "h2", "b0", "b1", "e0"])

    def test_empty_group_is_filled_from_the_others(self):
        brands = iter(f"B{i}" for i in range(100))
        rows = [item(f"h{i}", next(brands), f"Haarkur {i}", SHAMPOO, 4.8, 900 - i) for i in range(8)]
        db.upsert_products(self.conn, rows)
        self.assertEqual(len(self.pick(SHOP)), 6)

    def test_extra_group_needs_very_good_ratings(self):
        db.upsert_products(self.conn, [
            item("ok", "A", "Tagescreme", "Gesichtspflege", 4.6, 150),
            item("low", "B", "Serum", "Gesichtspflege; Serum & Kur", 4.4, 5000),   # below 4.5★
            item("few", "C", "Nachtcreme", "Gesichtspflege", 4.9, 60),             # below 100 reviews
        ])
        self.assertEqual(self.pick(SHOP), ["ok"])

    def test_price_cap_and_gift_set_exception(self):
        db.upsert_products(self.conn, [
            item("dear", "A", "Haarkur Luxus", SHAMPOO, 4.9, 3000, price=24.95),
            item("gift", "B", "Geschenkset Haarpflege", "Geschenksets", 4.9, 3000, price=29.95),
        ])
        self.assertEqual(self.pick(SHOP), ["gift"])

    def test_cheaper_ranks_above_dearer_at_equal_reviews(self):
        db.upsert_products(self.conn, [
            item("dear", "A", "Haarkur 1", SHAMPOO, 4.7, 1000, price=14.95),
            item("cheap", "B", "Haarkur 2", SHAMPOO, 4.7, 1000, price=4.95),
        ])
        self.assertEqual(self.pick(SHOP), ["cheap", "dear"])

    def test_never_sold_types_are_left_out(self):
        db.upsert_products(self.conn, [
            item("brush", "B", "Haarbürste", "Haarpflege", 4.9, 3000),
            item("vit", "C", "Vitamin D3 Tabletten", "Vitamine", 4.9, 3000),
            item("perf", "D", "Eau de Parfum", "Parfum", 4.9, 3000),
            item("jl", "Jean&Len", "Haarparfum Rose", "Haarstyling", 4.9, 3000),
        ])
        self.assertEqual(self.pick(SHOP), ["jl"])

    def test_flammable_products_are_skipped(self):
        db.upsert_products(self.conn, [item("1", "A", "Haarkur", SHAMPOO), item("2", "B", "Haarkur", SHAMPOO)])
        self.assertEqual(self.pick(SHOP, check=lambda p: (p["product_id"] != "1", "flammable")), ["2"])

    def test_many_reviews_beat_few_perfect_ones(self):
        self.assertGreater(shortlist.score(4.6, 3000, "", []), shortlist.score(5.0, 31, "", []))


    def test_shampoo_goes_out_with_its_conditioner(self):
        db.upsert_products(self.conn, [
            item("s1", "Wahre Schätze", "Shampoo Traube Hydraboost, 300 ml", SHAMPOO, 4.8, 2000),
            item("c1", "Wahre Schätze", "Conditioner Traube Hydraboost, 250 ml", "Conditioner", 4.6, 300),
            item("c2", "Wahre Schätze", "Conditioner Honig Schätze, 250 ml", "Conditioner", 4.9, 900),
        ])
        picks = shortlist.pick(self.conn, {**SHOP, "groups": {"hair": {"per_day": 1}}}, limit=1, check=safe)
        self.assertEqual([p["product_id"] for p in picks], ["s1"])
        self.assertEqual(picks[0]["pair"]["product_id"], "c1")

    def test_shampoo_without_conditioner_is_sold_alone(self):
        db.upsert_products(self.conn, [item("s1", "A", "Shampoo Lonely Line, 300 ml", SHAMPOO, 4.9, 3000)])
        picks = shortlist.pick(self.conn, SHOP, check=safe)
        self.assertEqual([p["product_id"] for p in picks], ["s1"])
        self.assertNotIn("pair", picks[0])

    def test_picked_conditioner_brings_its_shampoo_first(self):
        db.upsert_products(self.conn, [
            item("c1", "Syoss", "Conditioner Intense Keratin, 250 ml", "Conditioner", 4.9, 3000),
            item("s1", "Syoss", "Shampoo Intense Keratin, 440 ml", SHAMPOO, 4.5, 200),
        ])
        picks = shortlist.pick(self.conn, SHOP, check=safe)
        self.assertEqual(len(picks), 1)          # one post, not two
        self.assertEqual((picks[0]["product_id"], picks[0]["pair"]["product_id"]), ("s1", "c1"))

    def test_two_in_one_needs_no_pair(self):
        db.upsert_products(self.conn, [item("x", "P", "Shampoo & Conditioner 2in1, 250 ml", "Conditioner; Shampoo")])
        self.assertEqual(self.pick(SHOP), ["x"])

    def test_pair_is_not_redrafted(self):
        db.upsert_products(self.conn, [
            item("s1", "N", "Shampoo Power Repair, 250 ml", SHAMPOO, 4.8, 900),
            item("c1", "N", "Conditioner Power Repair, 200 ml", "Conditioner", 4.7, 400),
        ])
        db.add_draft(self.conn, "dm", "s1", {"product": {"pair": {"store": "dm", "product_id": "c1"}}})
        self.assertEqual(self.pick(SHOP), [])

    def test_unsafe_partner_means_shampoo_alone(self):
        db.upsert_products(self.conn, [
            item("s1", "N", "Shampoo Power Repair, 250 ml", SHAMPOO, 4.8, 900),
            item("c1", "N", "Conditioner Power Repair, 200 ml", "Conditioner", 4.7, 400),
        ])
        picks = shortlist.pick(self.conn, SHOP, check=lambda p: (p["product_id"] != "c1", "fire"))
        self.assertEqual([p["product_id"] for p in picks], ["s1"])
        self.assertNotIn("pair", picks[0])


GOOD_DRAFT = {
    "review_summary": "Review khen thơm.",
    "fit_for_vietnam": "Hợp tóc khô.",
    "labels": {
        "title": "Kem ủ tóc Balea Glow", "subtitle": "Dành cho tóc khô",
        "kind": "hair", "points": ["Ủ 5 phút sau khi gội", "Tóc mượt hơn"],
    },
    "captions": [
        {"style": "everyday story", "text": "Các babe ơi tuýp 200ml này thơm lắm nha 🥰"},
        {"style": "curiosity hook", "text": "Ủa sao thơm vậy nè, inbox sốp hỏi giá nha"},
        {"style": "engagement question", "text": "Các bà ủ tóc mấy lần một tuần nè?"},
        {"style": "direct sales", "text": "Order sốp nha, tuýp 200ml dùng cả tháng"},
    ],
}


class ImageLabelsTest(unittest.TestCase):
    def test_image_labels_new_shape(self):
        out = llm.image_labels(GOOD_DRAFT)
        self.assertEqual(out, {"title": "Kem ủ tóc Balea Glow", "subtitle": "Dành cho tóc khô",
                               "points": ["Ủ 5 phút sau khi gội", "Tóc mượt hơn"], "kind": "hair"})

    def test_image_labels_old_shape_maps_who_for_to_subtitle(self):
        out = llm.image_labels({"labels": {"title": "T", "hero": "Keratin", "who_for": "Dành cho x",
                                           "mode": "usage", "points": ["a", "b"]}})
        self.assertEqual(out, {"title": "T", "subtitle": "Dành cho x", "points": ["a", "b"], "kind": "other"})
        self.assertEqual(llm.image_labels({"labels": {"hero": "Keratin", "benefits": ["a"]}})["subtitle"], "Keratin")

    def test_bad_kind_is_a_warning_not_a_lost_draft(self):
        draft = {**GOOD_DRAFT, "labels": {**GOOD_DRAFT["labels"], "kind": "weird"}}
        self.assertEqual(llm.validate(draft), [])
        self.assertTrue(any(w.startswith("labels.kind must be") for w in llm.warnings(draft)))
        self.assertEqual(llm.image_labels(draft)["kind"], "other")

    def test_point_count_is_a_warning_and_clamped(self):
        draft = {**GOOD_DRAFT, "labels": {**GOOD_DRAFT["labels"], "points": ["a", "b", "c", "d"]}}
        self.assertEqual(llm.validate(draft), [])
        self.assertIn("labels.points must have 2–3 lines", llm.warnings(draft))
        self.assertEqual(llm.image_labels(draft)["points"], ["a", "b", "c"])

    def test_null_fields_do_not_crash(self):
        draft = {**GOOD_DRAFT, "labels": {"title": None, "subtitle": None, "kind": None, "points": None}}
        labels = llm.image_labels(draft)
        self.assertEqual((labels["title"], labels["subtitle"], labels["points"]), ("", "", []))
        self.assertIsInstance(llm.warnings(draft), list)
        self.assertEqual(llm.validate(draft), [])

    def test_warnings_title_too_long(self):
        draft = {**GOOD_DRAFT, "labels": {**GOOD_DRAFT["labels"], "title": "x" * 39}}
        self.assertTrue(any(w.startswith("label too long for the image") for w in llm.warnings(draft)))
        ok = {**GOOD_DRAFT, "labels": {**GOOD_DRAFT["labels"], "title": "x" * 38}}
        self.assertFalse(any(w.startswith("label too long") for w in llm.warnings(ok)))

    def test_prompt_picks_points_by_product_kind(self):
        for needle in ('"kind"', '"subtitle"', "hương đầu", "loại sản phẩm + hãng"):
            self.assertIn(needle, llm.USER_TEMPLATE)

    def test_card_shows_new_labels(self):
        card = discord.build_card(1, {"brand": "Balea", "name": "Kur", "rating": 4.1, "review_count": 9, "store": "dm"},
                                  {"shelf_eur": 2.75, "coupon_pct": 0, "cost_eur": 2.75, "sale_k": 115, "margin_k": 33},
                                  GOOD_DRAFT, [])
        desc = card["embeds"][0]["description"]
        self.assertIn("Kem ủ tóc Balea Glow", desc)
        self.assertIn("Dành cho tóc khô", desc)
        self.assertIn("▫️ Ủ 5 phút sau khi gội", desc)


class LlmOutputTest(unittest.TestCase):
    def test_good_draft_passes(self):
        self.assertEqual(llm.validate(GOOD_DRAFT), [])
        self.assertEqual(llm.warnings(GOOD_DRAFT), [])

    def test_forbidden_word_and_llm_prices_are_caught(self):
        bad = {**GOOD_DRAFT, "captions": [{"style": "a", "text": "Tiết kiệm tiền nha"},
                                          {"style": "b", "text": "Chỉ 120k thôi"},
                                          {"style": "c", "text": "Giá 2,75 €"},
                                          {"style": "d", "text": "Chỉ 325🐠/hũ"},
                                          {"style": "e", "text": "Chỉ {PRICE} thôi"}]}
        problems = llm.validate(bad)
        self.assertIn("caption 1 uses 'tiền'", problems)
        for i in (2, 3, 4, 5):
            self.assertIn(f"caption {i} mentions a price; captions must not contain prices", problems)

    def test_weight_loss_words_are_refused(self):
        for text in ["Giúp giảm cân nhanh", "đốt mỡ bụng", "giảm  mỡ hiệu quả", "tan mỡ", "Weight loss"]:
            bad = {**GOOD_DRAFT, "captions": [{"style": "a", "text": text}] * 3}
            self.assertTrue(any("sensitive word" in p for p in llm.validate(bad)), text)
        ok = {**GOOD_DRAFT, "captions": [{"style": "a", "text": "Giữ dáng nhẹ nhàng, cảm giác no lâu"}] * 3}
        self.assertEqual(llm.validate(ok), [])

    def test_prompt_describes_the_audience(self):
        system = llm.build_messages(product("1"), {}, [])[0]["content"]
        self.assertIn("học sinh cấp 3", system)
        self.assertIn("giảm cân", system)   # named as a forbidden word

    def test_sizes_are_not_mistaken_for_prices(self):
        ok = {**GOOD_DRAFT, "captions": [{"style": "a", "text": "Hũ 400ml, 10% Glycerin, 3.600 đánh giá 4.8 sao"}] * 3}
        self.assertEqual(llm.validate(ok), [])

    def test_draft_has_no_price_in_captions(self):
        from babebasket import cli

        with mock.patch.object(cli, "enrich", return_value=({}, [])), \
                mock.patch.object(llm, "draft_post", return_value=dict(GOOD_DRAFT)):
            payload = cli.make_draft(product("1"), CFG)
        self.assertEqual(payload["draft"]["captions"], GOOD_DRAFT["captions"])

    def test_parse_json_reply_with_code_fence(self):
        self.assertEqual(llm.parse_json_reply('Here:\n```json\n{"a": 1}\n```'), {"a": 1})

    def test_supplement_note_added(self):
        p = product("1")
        msgs = llm.build_messages(p, {"breadcrumbs": ["Gesundheit", "Nahrungsergänzungsmittel"]}, [])
        self.assertIn("thực phẩm chức năng", msgs[1]["content"])
        self.assertIn("KHÔNG ghi giá", msgs[0]["content"])
        self.assertNotIn("{PRICE}", msgs[0]["content"])


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


def _rossmann_category_page(products, grid_eans, split_at=None):
    """Minimal Next.js category page: grid tiles + flight data, like rossmann.de (Nov 2026)."""
    flight = 'fe:["$","div",null,{"children":[' + ",".join(
        json.dumps(["$", "$L100", None, {"product": p}]) for p in products
    ) + "]}]\n"
    chunks = [flight] if split_at is None else [flight[:split_at], flight[split_at:]]
    scripts = "".join(f"<script>self.__next_f.push({json.dumps([1, c])})</script>" for c in chunks)
    tiles = "".join(f'<div class="shadow-multi" data-item-enabled="true" data-item-ean="{e}"></div>' for e in grid_eans)
    return f'<div data-testid="search-products">{tiles}</div>{scripts}'


def _tile(ean, name="Haarkur", count=12, average=4.6, size="200 ml"):
    return {
        "ean": ean, "dan": "1", "productUrl": f"https://www.rossmann.de/de/x/p/{ean}", "name": name,
        "brandName": "Isana", "size": {"sizeText": size} if size else "$undefined",
        "price": {"retail": {"amount": 1.95, "currency": "EUR"}, "strikethrough": "$undefined",
                  "basePriceText": "(1 L = 9,75 €)"},
        "image": {"url": f"https://img/{ean}.png"}, "rating": {"average": average, "count": count},
    }


class RossmannNextPageTest(unittest.TestCase):
    def test_grid_tiles(self):
        page = _rossmann_category_page([_tile("111"), _tile("222", count=0, size=None)], ["111", "222"])
        rows = rossmann.parse_page(page, "T")
        self.assertEqual([r["product_id"] for r in rows], ["111", "222"])
        a, b = rows
        self.assertEqual((a["brand"], a["name"], a["price_eur"], a["unit_price"]), ("Isana", "Haarkur, 200 ml", 1.95, "1 L = 9,75 €"))
        self.assertEqual((a["rating"], a["review_count"], a["gtin"]), (4.6, 12, "111"))
        self.assertEqual((a["image_url"], a["product_url"]), ("https://img/111.png", "https://www.rossmann.de/de/x/p/111"))
        self.assertEqual((b["name"], b["rating"], b["review_count"]), ("Haarkur", None, 0))

    def test_only_grid_products_in_grid_order(self):
        page = _rossmann_category_page([_tile("111"), _tile("999"), _tile("222")], ["222", "111"])
        self.assertEqual([r["product_id"] for r in rossmann.parse_page(page)], ["222", "111"])

    def test_product_split_across_flight_chunks(self):
        page = _rossmann_category_page([_tile("111")], ["111"], split_at=60)
        self.assertEqual(len(rossmann.parse_page(page)), 1)

    def test_product_page_json_ld_gets_gtin_from_sku(self):
        page = ('<script type="application/ld+json">{"@type":"Product","name":"Badekristalle","sku":"4008233129761",'
                '"brand":{"name":"Kneipp"},"offers":{"price":0.95}}</script>')
        (r,) = rossmann.parse_page(page)
        self.assertEqual((r["gtin"], r["price_eur"]), ("4008233129761", 0.95))

    @unittest.skipUnless((ROOT / "rossmann_probe" / "category.html").exists(), "no saved probe page")
    def test_saved_probe_category_page(self):
        page = (ROOT / "rossmann_probe" / "category.html").read_text()
        if "Client Challenge" in page[:5000]:
            self.skipTest("saved page is the challenge page")
        rows = rossmann.parse_page(page)
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r["gtin"].isdigit() and r["name"] and r["price_eur"] and r["product_url"].startswith(rossmann.SITE))


def _png(color=(40, 90, 200), size=(300, 400)) -> bytes:
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


IMAGE_PAYLOAD = {
    "product": {"store": "dm", "product_id": "1", "brand": "Mixa", "name": "Urea", "image_url": "https://img/1.png"},
    "draft": GOOD_DRAFT,
}


class BuildImageTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "images" / "draft-1.jpg"
        self.gen_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _gen(self, result):
        def gen(packshot, product, cfg, **kw):
            self.gen_calls.append(product["product_id"])
            self.gen_kwargs = kw
            return result
        return gen

    def test_build_image_uses_scene(self):
        from babebasket import cli

        payload = dict(IMAGE_PAYLOAD)
        path = cli.build_image(payload, self.out, {}, fetch_bytes=lambda url: _png(), gen=self._gen(_png((9, 9, 9), (928, 1152))))
        self.assertEqual(path, str(self.out))
        self.assertTrue(self.out.exists())
        self.assertEqual((payload["image_path"], payload["image_source"]), (str(self.out), "gemini"))

    def test_build_image_keeps_the_raw_scene_and_passes_the_fix(self):
        from pathlib import Path

        from babebasket import cli

        payload = dict(IMAGE_PAYLOAD)
        cli.build_image(payload, self.out, {}, fetch_bytes=lambda url: _png(),
                        gen=self._gen(_png((9, 9, 9), (928, 1152))), feedback="brighter", previous=b"prev")
        self.assertEqual(self.gen_kwargs, {"feedback": "brighter", "previous": b"prev"})
        self.assertTrue(Path(payload["scene_path"]).exists())
        self.assertNotEqual(payload["scene_path"], payload["image_path"])

    def test_build_image_without_fix_passes_no_extras(self):
        from babebasket import cli

        cli.build_image(dict(IMAGE_PAYLOAD), self.out, {}, fetch_bytes=lambda url: _png(), gen=self._gen(None))
        self.assertEqual(self.gen_kwargs, {})

    def test_build_image_fallback_when_scene_none(self):
        from babebasket import cli

        payload = dict(IMAGE_PAYLOAD)
        cli.build_image(payload, self.out, {}, fetch_bytes=lambda url: _png(), gen=self._gen(None))
        self.assertEqual(payload["image_source"], "fallback")
        self.assertTrue(self.out.exists())

    def test_build_image_fallback_when_scene_is_not_an_image(self):
        from babebasket import cli

        payload = dict(IMAGE_PAYLOAD)
        cli.build_image(payload, self.out, {}, fetch_bytes=lambda url: _png(), gen=self._gen(b"not an image"))
        self.assertEqual(payload["image_source"], "fallback")

    def test_build_image_packshot_download_fails(self):
        from babebasket import cli

        def fail(url):
            raise RuntimeError("HTTP 404")

        payload = dict(IMAGE_PAYLOAD)
        self.assertIsNone(cli.build_image(payload, self.out, {}, fetch_bytes=fail, gen=self._gen(_png())))
        self.assertEqual(self.gen_calls, [])
        self.assertNotIn("image_path", payload)

    def test_build_image_without_image_url(self):
        from babebasket import cli

        payload = {**IMAGE_PAYLOAD, "product": {**IMAGE_PAYLOAD["product"], "image_url": ""}}
        self.assertIsNone(cli.build_image(payload, self.out, {}, fetch_bytes=lambda url: _png(), gen=self._gen(_png())))

    def test_set_payload_roundtrip(self):
        conn = db.connect(":memory:")
        draft_id = db.add_draft(conn, "dm", "1", {"a": 1})
        db.set_payload(conn, draft_id, {"a": 2, "image_path": "x.jpg"})
        row = db.drafts(conn, "drafted")[0]
        self.assertEqual(json.loads(row["payload"]), {"a": 2, "image_path": "x.jpg"})


CARD_PRODUCT = {"brand": "Balea", "name": "Kur", "rating": 4.1, "review_count": 9, "store": "dm",
                "image_url": "https://img/pack.png", "product_url": "https://www.dm.de/p/1"}
CARD_QUOTE = {"shelf_eur": 2.75, "coupon_pct": 0, "cost_eur": 2.75, "sale_k": 115, "margin_k": 33}


class DiscordImageTest(unittest.TestCase):
    def test_card_uses_attachment_when_image(self):
        card = discord.build_card(1, CARD_PRODUCT, CARD_QUOTE, GOOD_DRAFT, [], has_image=True)
        self.assertEqual(card["embeds"][0]["image"], {"url": "attachment://post.jpg"})
        self.assertEqual(card["attachments"], [{"id": 0, "filename": "post.jpg"}])

    def test_card_uses_packshot_without_image(self):
        card = discord.build_card(1, CARD_PRODUCT, CARD_QUOTE, GOOD_DRAFT, [])
        self.assertEqual(card["embeds"][0]["image"], {"url": "https://img/pack.png"})
        self.assertNotIn("attachments", card)

    def test_multipart_body(self):
        from babebasket import http

        body, ctype = http.encode_multipart({"payload_json": '{"a":1}'}, [("files[0]", "post.jpg", b"\xff\xd8JPEG", "image/jpeg")])
        boundary = ctype.split("boundary=")[1]
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="payload_json"', body)
        self.assertIn(b'name="files[0]"; filename="post.jpg"', body)
        self.assertIn(b"\xff\xd8JPEG", body)
        self.assertTrue(body.rstrip().endswith(f"--{boundary}--".encode()))

    def test_post_card_uploads_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"\xff\xd8x")
            f.flush()
            with mock.patch.object(discord, "request_multipart", return_value={"id": "m1"}) as up, \
                 mock.patch.object(discord, "_call") as call, mock.patch.object(discord.time, "sleep"):
                mid = discord.post_card("c1", {"content": "x"}, 2, image_path=f.name)
        self.assertEqual(mid, "m1")
        kwargs = up.call_args.kwargs
        self.assertEqual(kwargs["files"][0][:2], ("files[0]", "post.jpg"))
        self.assertEqual(json.loads(kwargs["fields"]["payload_json"]), {"content": "x"})
        self.assertEqual(call.call_count, 4)  # 2 numbers + ✅ + ❌ reactions


class PostResilienceTest(unittest.TestCase):
    def _payload(self, image_path):
        return {"product": CARD_PRODUCT, "quote": CARD_QUOTE, "draft": GOOD_DRAFT, "warnings": [], "image_path": image_path}

    def test_upload_failure_falls_back_to_card_without_image(self):
        import tempfile
        from babebasket import cli

        conn = db.connect(":memory:")
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            draft_id = db.add_draft(conn, "dm", "1", self._payload(f.name))
            calls = []

            def post_card(channel, card, n, image_path=None):
                calls.append(image_path)
                if image_path:
                    raise RuntimeError("HTTP 400")
                return "m2"

            with mock.patch.object(discord, "post_card", side_effect=post_card), mock.patch.object(cli.time, "sleep"):
                cli.cmd_post({"discord": {"channel_id": "c"}}, conn)
        self.assertEqual(calls, [f.name, None])
        row = conn.execute("SELECT status, discord_message_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        self.assertEqual((row["status"], row["discord_message_id"]), ("pending", "m2"))

    def test_one_failing_draft_does_not_stop_the_rest(self):
        from babebasket import cli

        conn = db.connect(":memory:")
        db.add_draft(conn, "dm", "1", self._payload(None))
        second = db.add_draft(conn, "dm", "2", self._payload(None))
        results = iter([RuntimeError("HTTP 500"), "m2"])

        def post_card(channel, card, n, image_path=None):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(discord, "post_card", side_effect=post_card), mock.patch.object(cli.time, "sleep"):
            cli.cmd_post({"discord": {"channel_id": "c"}}, conn)
        row = conn.execute("SELECT status FROM drafts WHERE id = ?", (second,)).fetchone()
        self.assertEqual(row["status"], "pending")

    def test_multipart_retries_429(self):
        import io
        import urllib.error
        from babebasket import http

        err = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "0.01"}, io.BytesIO(b"{}"))
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"id": "m1"}'
        with mock.patch.object(http.urllib.request, "urlopen", side_effect=[err, ok]) as op, \
             mock.patch.object(http.time, "sleep"):
            out = http.request_multipart("https://x", fields={"a": "1"}, files=[])
        self.assertEqual((out, op.call_count), ({"id": "m1"}, 2))


class StoreBadgeTest(unittest.TestCase):
    def _card(self, store):
        return discord.build_card(5, {**CARD_PRODUCT, "store": store}, CARD_QUOTE, GOOD_DRAFT, [])

    def test_dm_badge(self):
        card = self._card("dm")
        self.assertIn("🟦 DM", card["content"])
        self.assertIn({"name": "Nguồn", "value": "🟦 DM", "inline": True}, card["embeds"][0]["fields"])

    def test_rossmann_badge(self):
        card = self._card("rossmann")
        self.assertIn("🟥 ROSSMANN", card["content"])
        self.assertIn({"name": "Nguồn", "value": "🟥 ROSSMANN", "inline": True}, card["embeds"][0]["fields"])
        self.assertEqual(card["embeds"][0]["color"], discord.STORE_COLORS["rossmann"])


ROSSMANN_PRODUCT = {
    "store": "rossmann", "product_id": "4262569110125", "gtin": "4262569110125", "brand": "Haarwald",
    "name": "Styling Powder, 20 g", "price_eur": 9.99, "unit_price": "1 kg = 499,50 €", "rating": 4.6,
    "review_count": 40, "categories": "", "image_url": "https://www.rossmann.de/media-neu/x.png",
    "product_url": "https://www.rossmann.de/de/p/4262569110125",
}


class RossmannFlowTest(unittest.TestCase):
    def test_prompt_builds_without_dm_details(self):
        from babebasket import cli

        detail, reviews = cli.enrich(dict(ROSSMANN_PRODUCT))
        messages = llm.build_messages(ROSSMANN_PRODUCT, detail, reviews)
        self.assertIn("Haarwald", messages[1]["content"])
        self.assertIn("rossmann", messages[1]["content"])

    def test_card_and_image_for_rossmann(self):
        import tempfile
        from pathlib import Path
        from babebasket import cli

        quote = pricing.quote(ROSSMANN_PRODUCT["price_eur"], "rossmann", CFG)
        card = discord.build_card(9, ROSSMANN_PRODUCT, quote, GOOD_DRAFT, [], has_image=True)
        self.assertIn("🟥 ROSSMANN", card["content"])
        with tempfile.TemporaryDirectory() as d:
            payload = {"product": dict(ROSSMANN_PRODUCT), "draft": GOOD_DRAFT}
            path = cli.build_image(payload, Path(d) / "r.jpg", {}, fetch_bytes=lambda url: _png(), gen=lambda *a: None)
            self.assertTrue(Path(path).exists())


class TryRossmannTest(unittest.TestCase):
    def test_product_from_saved_page(self):
        import tempfile
        from pathlib import Path
        from babebasket import cli

        page = _rossmann_category_page([_tile("111"), _tile("222", name="Kur")], ["111", "222"])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "category.html"
            f.write_text(page)
            self.assertEqual(cli.rossmann_product(f, "222")["name"], "Kur, 200 ml")
            self.assertEqual(cli.rossmann_product(f, None)["product_id"], "111")
            with self.assertRaises(SystemExit):
                cli.rossmann_product(f, "999")


class NoReviewsTest(unittest.TestCase):
    def test_prompt_forbids_invented_reviews_when_none(self):
        content = llm.build_messages(ROSSMANN_PRODUCT, {"claims": [], "breadcrumbs": [], "description": ""}, [])[1]["content"]
        self.assertIn(llm.NO_REVIEWS_NOTE.split("{rating}")[0].strip(), content)
        self.assertIn(llm.NO_DETAILS_NOTE.strip(), content)
        self.assertIn("4.6/5 (40 lượt đánh giá)", content)

    def test_prompt_unchanged_when_reviews_exist(self):
        review = {"rating": 5, "title": "Top", "text": "Super", "reviewer": "A", "incentivized": False}
        detail = {"claims": ["pflegt"], "breadcrumbs": ["Haare"], "description": "Eine Kur"}
        content = llm.build_messages(ROSSMANN_PRODUCT, detail, [review])[1]["content"]
        self.assertNotIn(llm.NO_REVIEWS_NOTE.split("{rating}")[0].strip(), content)
        self.assertNotIn(llm.NO_DETAILS_NOTE.strip(), content)

    def test_card_warns_when_drafted_without_reviews(self):
        self.assertIn(llm.NO_REVIEWS_WARNING, llm.warnings(GOOD_DRAFT, had_reviews=False))
        self.assertNotIn(llm.NO_REVIEWS_WARNING, llm.warnings(GOOD_DRAFT))


class RossmannNameSizeTest(unittest.TestCase):
    def test_size_not_repeated_when_name_has_it(self):
        page = _rossmann_category_page([_tile("111", name="Professional Plex Shampoo, 250ml", size="250 ml")], ["111"])
        self.assertEqual(rossmann.parse_page(page)[0]["name"], "Professional Plex Shampoo, 250ml")

    def test_size_added_when_missing(self):
        page = _rossmann_category_page([_tile("111", name="Plex Maske", size="125 ml")], ["111"])
        self.assertEqual(rossmann.parse_page(page)[0]["name"], "Plex Maske, 125 ml")
