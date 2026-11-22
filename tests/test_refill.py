"""A ❌ today gets a replacement draft, so the day keeps its per_day posts."""

import json
import unittest
from unittest import mock

from babebasket import cli, db, shortlist

CFG = {"shortlist": {"per_day": 3, "refill_max_per_day": 2},
       "groups": {"hair": {"per_day": 2}, "body": {"per_day": 1}}}


def add(conn, pid, group, status, brand="B"):
    payload = {"product": {"store": "dm", "product_id": pid, "brand": brand, "name": f"N {pid}", "group": group},
               "draft": {"captions": []}}
    draft_id = db.add_draft(conn, "dm", pid, payload)
    db.set_status(conn, draft_id, status)
    return draft_id


class RefillTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.asked = []

        def pick(conn, cfg, limit=None, check=None, only_group=None, today=None):
            self.asked.append((only_group, [p["product_id"] for p in today]))
            n = len(self.asked)
            return [{"store": "dm", "product_id": f"new{n}", "brand": "B", "name": f"New {n}", "group": only_group}]

        self.patches = [mock.patch.object(shortlist, "pick", side_effect=pick),
                        mock.patch.object(cli, "make_draft", side_effect=lambda p, cfg: {"product": p, "draft": {}})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_nothing_drafted_today_means_no_refill(self):
        self.assertEqual(cli.cmd_refill(CFG, self.conn), [])
        self.assertEqual(self.asked, [])

    def test_a_rejected_hair_post_is_replaced_by_hair(self):
        add(self.conn, "h1", "hair", "pending")
        add(self.conn, "h2", "hair", "rejected")
        add(self.conn, "b1", "body", "approved")
        new = cli.cmd_refill(CFG, self.conn)
        self.assertEqual(len(new), 1)
        self.assertEqual(self.asked, [("hair", ["h1", "h2", "b1"])])   # rejected one is never offered again
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (new[0],)).fetchone()
        self.assertEqual(row["status"], "drafted")                     # its card goes out with `post`
        self.assertTrue(json.loads(row["payload"])["refill"])
        self.assertEqual(cli.cmd_refill(CFG, self.conn), [])          # day is full again

    def test_refills_stop_at_the_daily_cap(self):
        for i in range(3):
            add(self.conn, f"h{i}", "hair", "rejected")
        self.assertEqual(len(cli.cmd_refill(CFG, self.conn)), 2)      # 3 missing, cap 2
        self.assertEqual([g for g, _ in self.asked], ["hair", "hair"])
        self.assertEqual(cli.cmd_refill(CFG, self.conn), [])

    def test_yesterdays_drafts_do_not_count(self):
        draft_id = add(self.conn, "old", "hair", "rejected")
        self.conn.execute("UPDATE drafts SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (draft_id,))
        self.assertEqual(cli.cmd_refill(CFG, self.conn), [])


class PickTodayTest(unittest.TestCase):
    def test_refill_keeps_two_per_brand_for_the_whole_day(self):
        conn = db.connect(":memory:")
        rows = [{"store": "dm", "product_id": f"p{i}", "brand": brand, "name": f"Shampoo Linie{i}, 250 ml",
                 "price_eur": 3.0, "rating": 4.6, "review_count": 200, "categories": "Shampoo"}
                for i, brand in enumerate(["Gliss", "Balea"])]
        db.upsert_products(conn, rows)
        cfg = {"shortlist": {"per_day": 3, "repeat_after_days": 45, "max_price_eur": 20, "min_rating": 4.0,
                             "min_reviews": 10}, "groups": {"hair": {"per_day": 3}}}
        today = [{"store": "dm", "product_id": "x1", "brand": "Gliss", "name": "A"},
                 {"store": "dm", "product_id": "x2", "brand": "Gliss", "name": "B"}]
        picks = shortlist.pick(conn, cfg, limit=1, check=lambda p: (True, ""), only_group="hair", today=today)
        self.assertEqual([p["brand"] for p in picks], ["Balea"])


if __name__ == "__main__":
    unittest.main()
