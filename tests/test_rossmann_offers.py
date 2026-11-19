"""Rossmann's public offers pages: discounts rank products higher; the price already includes them."""

import unittest
from unittest import mock

from babebasket import catalog, cli, coupons, db, discord, shortlist
from babebasket.stores import rossmann

TILE = {"ean": "4305615", "name": "Duschgel Extra-XL 3-in-1 Captain", "brandName": "Old Spice",
        "productUrl": "/de/p/4305615", "size": {"sizeText": "1.000 ml"},
        "price": {"retail": {"amount": 6.63}, "strikethrough": {"amount": 8.29}, "promotionLabel": "NO_PROMOTION"},
        "disruptors": [{"type": "BULK_DISCOUNT", "text": "20% Sparen"}, {"type": "ADVERTISING", "text": "Aus der Werbung"}],
        "rating": {"average": 4.6, "count": 40}}


class TileTest(unittest.TestCase):
    def test_badge_and_flyer(self):
        row = rossmann._row_from_tile(TILE, "")
        self.assertEqual((row["offer_pct"], row["offer_label"]), (20.0, "20% Sparen · Aus der Werbung"))
        self.assertEqual(row["price_eur"], 6.63)

    def test_old_price_without_badge(self):
        tile = dict(TILE, disruptors=[])
        self.assertEqual(rossmann._row_from_tile(tile, "")["offer_pct"], 20)   # 1 - 6.63/8.29

    def test_new_flag_and_no_offer(self):
        tile = dict(TILE, disruptors=[{"type": "NEW", "text": "Neu"}], price={"retail": {"amount": 0.55}})
        row = rossmann._row_from_tile(tile, "")
        self.assertEqual((row["offer_pct"], row["is_new_flag"]), (0.0, True))


class OfferCouponTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def row(self, pid, pct, store="rossmann"):
        return {"store": store, "product_id": pid, "offer_pct": pct, "offer_label": "20% Sparen"}

    def test_offers_replace_yesterdays(self):
        coupons.replace_store_offers(self.conn, [self.row("a", 20), self.row("b", 0)])
        coupons.replace_store_offers(self.conn, [self.row("c", 15)])
        live = coupons.active(self.conn)
        self.assertEqual([c["product_ids"] for c in live], [["c"]])
        self.assertTrue(live[0]["in_price"])

    def test_offer_matches_only_that_product(self):
        coupons.replace_store_offers(self.conn, [self.row("a", 20)])
        live = coupons.active(self.conn)
        self.assertTrue(coupons.matches(live[0], {"store": "rossmann", "product_id": "a"}))
        self.assertFalse(coupons.matches(live[0], {"store": "rossmann", "product_id": "b"}))
        self.assertFalse(coupons.matches(live[0], {"store": "dm", "product_id": "a"}))

    def test_offer_is_not_subtracted_twice(self):
        cfg = {"pricing": {"vnd_per_eur": 30000, "markup": 0.35}, "coupons": {"rossmann": 0}, "llm": {"model": "m"}}
        product = {"store": "rossmann", "product_id": "a", "price_eur": 6.63, "brand": "Old Spice", "name": "Duschgel",
                   "rating": 4.6, "review_count": 40,
                   "coupon": {"percent": 20, "in_price": True, "label": "Rossmann −20%"}}
        with mock.patch.object(cli, "enrich", return_value=({}, [])), \
                mock.patch.object(cli.llm, "draft_post", return_value={"captions": []}):
            payload = cli.make_draft(product, cfg)
        self.assertEqual(payload["quote"]["cost_eur"], 6.63)

    def test_old_databases_get_the_new_columns(self):
        conn = db.connect(":memory:")
        conn.execute("CREATE TABLE coupons (id INTEGER PRIMARY KEY, store TEXT NOT NULL, applies_to TEXT NOT NULL,"
                     " brands TEXT NOT NULL DEFAULT '[]', keywords TEXT NOT NULL DEFAULT '[]', percent REAL,"
                     " payback_multiplier REAL, min_spend_eur REAL, valid_until TEXT, label TEXT,"
                     " source_message_id TEXT, reply_message_id TEXT, active INTEGER NOT NULL DEFAULT 1,"
                     " created_at TEXT NOT NULL)")
        coupons.ensure(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(coupons)")}
        self.assertTrue({"product_ids", "in_price", "source"} <= cols)


class FoodAndDecorTest(unittest.TestCase):
    def test_not_sold(self):
        for name, cats in [("Chips Adventskalender 2026", ""), ("Schwebende LED-Kerzen 10er-Set", ""),
                           ("Schoko Adventskalender", "Geschenksets"),
                           ("Roland Kaiser Adventskalender 2026, 160 g", ""), ("Adventskalender 2026, 200 g", "")]:
            self.assertIsNone(catalog.classify({"name": name, "categories": cats, "brand": ""})[0], name)
        self.assertEqual(catalog.classify({"name": "Beauty Adventskalender 2026", "categories": "", "brand": "ahead"})[0],
                         "extra")


class PostFailureTest(unittest.TestCase):
    def test_failed_cards_make_the_run_fail_and_stay_drafted(self):
        conn = db.connect(":memory:")
        draft_id = db.add_draft(conn, "dm", "1", {"product": {"store": "dm", "brand": "B", "name": "N", "rating": 4.5,
                                                              "review_count": 10},
                                                  "quote": {"shelf_eur": 1, "coupon_pct": 0, "cost_eur": 1},
                                                  "draft": {"captions": [{"text": "x"}]}, "warnings": []})
        with mock.patch.object(discord, "post_card", side_effect=RuntimeError("HTTP 401")), \
                mock.patch.object(cli.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                cli.cmd_post({"discord": {"channel_id": "CH"}}, conn)
        self.assertIn("DISCORD_BOT_TOKEN", str(ctx.exception))
        self.assertEqual(conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0], "drafted")


if __name__ == "__main__":
    unittest.main()
