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


class DoubleRunRegressionTest(RefillTest):
    """2026-11-23: `check` refilled while `daily` was still drafting, and both posted the same drafts."""

    def test_a_day_that_is_only_partly_drafted_is_not_refilled(self):
        add(self.conn, "h1", "hair", "pending")          # daily has drafted 1 of 3 so far, nothing rejected
        self.assertEqual(cli.cmd_refill(CFG, self.conn), [])
        self.assertEqual(self.asked, [])

    def test_no_refill_while_daily_is_running(self):
        add(self.conn, "h1", "hair", "rejected")
        with mock.patch.object(cli, "daily_running", return_value=True):
            self.assertEqual(cli.cmd_refill(CFG, self.conn), [])
        self.assertEqual(len(cli.cmd_refill(CFG, self.conn)), 1)   # once daily is done, the ❌ is replaced

    def test_daily_running_reads_the_run_lock(self):
        import os
        import tempfile
        from pathlib import Path
        locks = Path(tempfile.mkdtemp())
        self.assertFalse(cli.daily_running(locks))
        (locks / "daily.lock").mkdir()
        (locks / "daily.lock" / "pid").write_text(str(os.getpid()))
        self.assertTrue(cli.daily_running(locks))
        (locks / "daily.lock" / "pid").write_text("999999")       # a crashed run's lock does not count
        self.assertFalse(cli.daily_running(locks))


class PostClaimTest(unittest.TestCase):
    CFG = {"discord": {"channel_id": "CH"}}

    def setUp(self):
        self.conn = db.connect(":memory:")
        payload = {"product": {"store": "dm", "product_id": "p", "brand": "B", "name": "N"}, "quote": {},
                   "draft": {"captions": [{"text": "a"}]}, "warnings": []}
        self.draft = db.add_draft(self.conn, "dm", "p", payload)

    def test_a_draft_another_run_already_claimed_is_not_sent_again(self):
        from babebasket import discord
        sent = []

        def post_card(channel, card, n, image_path=None):
            # while this run is sending, the other run tries the same draft
            sent.append(1)
            with mock.patch.object(discord, "post_card", side_effect=lambda *a, **k: sent.append(2) or "M2"):
                cli.cmd_post(self.CFG, self.conn)
            return "M1"

        with mock.patch.object(discord, "build_card", return_value={}), \
                mock.patch.object(discord, "post_card", side_effect=post_card), mock.patch.object(cli.time, "sleep"):
            cli.cmd_post(self.CFG, self.conn)
        self.assertEqual(sent, [1])
        row = self.conn.execute("SELECT status, discord_message_id FROM drafts WHERE id = ?", (self.draft,)).fetchone()
        self.assertEqual((row["status"], row["discord_message_id"]), ("pending", "M1"))

    def test_a_failed_send_is_released_for_the_next_post(self):
        from babebasket import discord
        with mock.patch.object(discord, "build_card", return_value={}), \
                mock.patch.object(discord, "post_card", side_effect=RuntimeError("HTTP 500")):
            with self.assertRaises(RuntimeError):
                cli.cmd_post(self.CFG, self.conn)
        status = self.conn.execute("SELECT status FROM drafts WHERE id = ?", (self.draft,)).fetchone()["status"]
        self.assertEqual(status, "drafted")
