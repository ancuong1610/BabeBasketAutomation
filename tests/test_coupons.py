import datetime as dt
import json
import unittest
from unittest import mock

from babebasket import coupons, db, discord, pricing, shortlist

BALEA = {"store": "dm", "applies_to": "brands", "brands": ["Balea"], "keywords": [], "percent": 20,
         "valid_until": "2099-01-01", "label": "Balea −20%"}
HAIR = {"store": "dm", "applies_to": "categories", "brands": [], "keywords": ["Shampoo", "Spülung", "Haarkur"],
        "payback_multiplier": 10, "valid_until": "2099-01-01", "label": "Haarpflege 10-fach"}


def prod(name, brand="Balea", store="dm", cats=""):
    return {"store": store, "brand": brand, "name": name, "categories": cats}


class MatchTest(unittest.TestCase):
    def test_brand_coupon(self):
        self.assertTrue(coupons.matches(BALEA, prod("Bodylotion Urea")))
        self.assertFalse(coupons.matches(BALEA, prod("Bodylotion", brand="NIVEA")))
        self.assertFalse(coupons.matches(BALEA, prod("Bodylotion", store="rossmann")))   # dm coupon only at dm

    def test_category_keywords(self):
        self.assertTrue(coupons.matches(HAIR, prod("Shampoo Repair", brand="GLISS")))
        self.assertFalse(coupons.matches(HAIR, prod("Duschgel", brand="GLISS")))

    def test_brand_plus_line(self):
        c = dict(BALEA, keywords=["Haarkur"])
        self.assertTrue(coupons.matches(c, prod("Haarkur Glow")))
        self.assertFalse(coupons.matches(c, prod("Duschgel")))

    def test_whole_purchase(self):
        self.assertTrue(coupons.matches({"store": "dm", "applies_to": "all"}, prod("anything", brand="X")))

    def test_payback_is_worth_half_a_percent_per_multiple(self):
        self.assertEqual(coupons.effective_pct(HAIR), 5.0)
        self.assertEqual(coupons.effective_pct(BALEA), 20.0)

    def test_best_coupon_wins(self):
        best = coupons.best_for(prod("Shampoo Repair"), [BALEA, HAIR])
        self.assertEqual(best["label"], "Balea −20%")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_expired_coupons_stop_counting(self):
        coupons.save(self.conn, [dict(BALEA, valid_until="2026-11-20"), dict(HAIR, valid_until=None)], "m1")
        on_day = [c["label"] for c in coupons.active(self.conn, on=dt.date(2026, 11, 20))]
        after = [c["label"] for c in coupons.active(self.conn, on=dt.date(2026, 11, 21))]
        self.assertEqual(sorted(on_day), ["Balea −20%", "Haarpflege 10-fach"])
        self.assertEqual(after, ["Haarpflege 10-fach"])   # no end date: stays until deleted

    def test_shortlist_boosts_products_with_a_coupon(self):
        from tests.test_pipeline import SHOP, item, safe

        db.upsert_products(self.conn, [item("plain", "NIVEA", "Haarkur A", "Haarkur & Haarmaske", 4.8, 2000),
                                       item("deal", "Garnier", "Haarkur B", "Haarkur & Haarmaske", 4.7, 1800)])
        self.assertEqual([p["product_id"] for p in shortlist.pick(self.conn, SHOP, check=safe)], ["plain", "deal"])
        coupons.save(self.conn, [dict(BALEA, brands=["Garnier"])], "m1")
        picks = shortlist.pick(self.conn, SHOP, check=safe)
        self.assertEqual([p["product_id"] for p in picks], ["deal", "plain"])
        self.assertEqual(picks[0]["coupon"]["percent"], 20)

    def test_whole_purchase_coupon_lowers_cost_but_not_ranking(self):
        from tests.test_pipeline import SHOP, item, safe

        db.upsert_products(self.conn, [item("a", "NIVEA", "Haarkur A", "Haarkur & Haarmaske", 4.8, 2000),
                                       item("b", "Garnier", "Haarkur B", "Haarkur & Haarmaske", 4.7, 1800)])
        coupons.save(self.conn, [{"store": "dm", "applies_to": "all", "percent": 10, "label": "−10% cả hoá đơn"}], "m")
        picks = shortlist.pick(self.conn, SHOP, check=safe)
        self.assertEqual([p["product_id"] for p in picks], ["a", "b"])
        self.assertEqual(picks[0]["coupon"]["percent"], 10)


class PriceAndCardTest(unittest.TestCase):
    CFG = {"pricing": {"vnd_per_eur": 30000, "markup": 0.35}, "coupons": {"dm": 0, "rossmann": 10}}

    def test_coupon_lowers_cost(self):
        self.assertEqual(pricing.quote(10.0, "dm", self.CFG, 20)["cost_eur"], 8.0)
        self.assertEqual(pricing.quote(10.0, "rossmann", self.CFG, 5)["coupon_pct"], 10)   # standing 10 % is bigger

    def test_card_shows_the_coupon(self):
        from tests.test_pipeline import GOOD_DRAFT, product

        p = dict(product("1"), coupon={"label": "Balea −20%", "percent": 20, "valid_until": "2026-11-30"})
        card = discord.build_card(1, p, {"shelf_eur": 3.0, "coupon_pct": 20, "cost_eur": 2.4}, GOOD_DRAFT, [])
        field = [f["value"] for f in card["embeds"][0]["fields"] if f["name"] == "🎟️ Coupon"][0]
        self.assertEqual(field, "Balea −20% (−20%) · đến 30.11")


def reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


class ChannelTest(unittest.TestCase):
    CFG = {"discord": {"channel_id": "APP", "owner_user_ids": ["42"], "coupons_channel_id": "COUP"}, "llm": {"model": "m"}}

    def setUp(self):
        self.conn = db.connect(":memory:")
        self.sent, self.replies = [], []

    def run_channel(self, messages, llm_answer, taps=()):
        def call(method, path, body=None):
            if method == "GET" and "/messages" in path:
                return messages
            if method == "POST":
                self.sent.append(body)
                return {"id": f"R{len(self.sent)}"}
            return {}
        post = mock.Mock(return_value=reply(llm_answer))
        with mock.patch.object(discord, "_call", side_effect=call), \
                mock.patch.object(discord, "_react"), \
                mock.patch.object(discord, "owner_taps", return_value=set(taps)), \
                mock.patch.object(discord, "reply", side_effect=lambda ch, mid, t: self.replies.append(t)):
            coupons.process_channel(self.CFG, self.conn, fetch=lambda url: b"img", post=post)
        return post

    def test_screenshot_is_read_saved_and_confirmed(self):
        msg = {"id": "10", "author": {"id": "42"}, "content": "",
               "attachments": [{"url": "https://cdn/x.png", "content_type": "image/png"}]}
        answer = {"coupons": [{"store": "dm", "applies_to": "brands", "brands": ["Balea"], "percent": 20,
                               "valid_until": "2026-11-30", "label": "Balea −20%"},
                              {"store": "dm", "applies_to": "all", "percent": 0, "label": "free sample"}]}
        post = self.run_channel([msg], answer)
        self.assertEqual(post.call_args.kwargs["body"]["messages"][0]["content"][1]["type"], "image_url")
        saved = coupons.active(self.conn, on=dt.date(2026, 11, 1))
        self.assertEqual([c["label"] for c in saved], ["Balea −20%"])          # 0 % entries are dropped
        self.assertIn("🎟️ Đã lưu 1 coupon", self.sent[0]["content"])
        self.assertIn("đến 30.11", self.sent[0]["content"])
        self.assertEqual(saved[0]["reply_message_id"], "R1")

    def test_messages_are_read_once_and_strangers_ignored(self):
        msgs = [{"id": "10", "author": {"id": "99"}, "content": "dm 50% alles"},
                {"id": "11", "author": {"id": "42"}, "content": "dm 20% Balea đến 30.11"}]
        self.run_channel(msgs, {"coupons": [dict(BALEA)]})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(coupons._kv(self.conn, "coupons_last_message_id"), "11")

    def test_cross_on_the_reply_deletes_those_coupons(self):
        ids = coupons.save(self.conn, [BALEA], "10")
        self.conn.execute("UPDATE coupons SET reply_message_id = 'R1' WHERE id = ?", (ids[0],))
        self.run_channel([], {"coupons": []}, taps=[coupons.DELETE])
        self.assertEqual(coupons.active(self.conn), [])
        self.assertIn("Đã xoá", self.replies[0])

    def test_unreadable_post_gets_a_reply(self):
        msg = {"id": "12", "author": {"id": "42"}, "content": "xem coupon này"}
        with mock.patch.object(coupons, "extract", side_effect=RuntimeError("bad json")):
            self.run_channel([msg], {})
        self.assertIn("Không đọc được", self.replies[0])


if __name__ == "__main__":
    unittest.main()
