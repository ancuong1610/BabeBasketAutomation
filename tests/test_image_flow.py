"""Two-step approval: the AI image is made only after the caption is approved, then approved itself."""

import json
import unittest
from unittest import mock

from babebasket import cli, db, discord, threads

CFG = {
    "discord": {"channel_id": "CH", "owner_user_ids": []},
    "image": {"enabled": True, "max_per_day": 4, "max_per_post": 2},
    "threads": {"enabled": True},
}


def caption_approved(conn, product_id="1"):
    payload = {"product": {"store": "dm", "product_id": product_id, "brand": "B", "name": "N",
                           "image_url": "https://img"},
               "quote": {}, "warnings": [],
               "draft": {"captions": [{"style": "a", "text": "Caption một"}]}}
    draft_id = db.add_draft(conn, "dm", product_id, payload)
    db.set_status(conn, draft_id, "approved", discord_message_id="CAPTION", chosen_caption=1)
    return draft_id


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.builds, self.cards, self.replies = [], [], []

        def fake_build(payload, out, cfg, ai=True, **kw):
            self.builds.append(ai)
            payload["image_path"], payload["image_source"] = str(out), "gemini" if ai else "fallback"
            return str(out)

        def fake_post_image_card(channel, card, path, tries, source):
            self.cards.append({"card": card, "tries": tries, "source": source})
            return f"IMG{len(self.cards)}"

        self.patches = [
            mock.patch.object(cli, "build_image", side_effect=fake_build),
            mock.patch.object(discord, "post_image_card", side_effect=fake_post_image_card),
            mock.patch.object(discord, "reply", side_effect=lambda ch, mid, text: self.replies.append(text)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def row(self, draft_id):
        r = self.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return r["status"], json.loads(r["payload"])

    def decide(self, decision):
        with mock.patch.object(discord, "read_image_decision", return_value=decision):
            cli.cmd_check_images(CFG, self.conn)

    def test_draft_makes_no_image(self):
        product = {"store": "dm", "product_id": "1", "brand": "B", "name": "N"}
        with mock.patch.object(cli.shortlist, "pick", return_value=[product]), \
                mock.patch.object(cli, "make_draft", return_value={"draft": {}}):
            cli.cmd_draft(CFG, self.conn, None)
        self.assertEqual(self.builds, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM image_calls").fetchone()[0], 0)

    def test_approved_caption_gets_ai_image_card(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        status, payload = self.row(draft_id)
        self.assertEqual(status, "image_pending")
        self.assertEqual(payload["image_message_id"], "IMG1")
        self.assertEqual(self.builds, [True])
        self.assertEqual(db.image_calls_for(self.conn, draft_id), 1)
        self.assertEqual(self.cards[0]["tries"], 1)  # one 🔄 left
        self.assertEqual(self.cards[0]["card"]["message_reference"]["message_id"], "CAPTION")

    def test_accept_makes_it_ready(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        self.decide("accept")
        self.assertEqual(self.row(draft_id)[0], "ready")

    def test_reject_drops_post(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        self.decide("reject")
        self.assertEqual(self.row(draft_id)[0], "rejected")

    def test_retry_makes_a_new_ai_image_then_runs_out(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        self.decide("retry")
        status, payload = self.row(draft_id)
        self.assertEqual(status, "image_pending")
        self.assertEqual(payload["image_message_id"], "IMG2")
        self.assertEqual(self.builds, [True, True])
        self.assertEqual(self.cards[1]["tries"], 0)  # no more 🔄 offered
        self.decide("retry")  # tapped anyway on the old card: falls back to plain, no paid call
        self.assertEqual(self.builds, [True, True, False])
        self.assertEqual(db.image_calls_for(self.conn, draft_id), 2)
        self.assertIn("limit reached", self.cards[2]["card"]["content"])

    def test_plain_costs_nothing(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        self.decide("plain")
        self.assertEqual(self.builds, [True, False])
        self.assertEqual(db.image_calls_for(self.conn, draft_id), 1)
        self.assertEqual(self.cards[1]["source"], "fallback")

    def test_daily_cap_across_drafts(self):
        cfg = {**CFG, "image": {"enabled": True, "max_per_day": 2, "max_per_post": 2}}
        ids = [caption_approved(self.conn, str(i)) for i in range(3)]
        cli.cmd_images(cfg, self.conn)
        self.assertEqual(self.builds, [True, True, False])
        self.assertEqual(self.cards[2]["source"], "fallback")
        self.assertEqual([self.row(i)[0] for i in ids], ["image_pending"] * 3)

    def test_disabled_never_calls_ai(self):
        caption_approved(self.conn)
        cli.cmd_images({**CFG, "image": {"enabled": False}}, self.conn)
        self.assertEqual(self.builds, [False])

    def test_no_product_photo_goes_text_only(self):
        draft_id = caption_approved(self.conn)
        payload = self.row(draft_id)[1]
        payload["product"]["image_url"] = ""
        db.set_payload(self.conn, draft_id, payload)
        cli.cmd_images(CFG, self.conn)
        self.assertEqual(self.row(draft_id)[0], "ready")
        self.assertIn("text only", self.replies[0])
        self.assertEqual(self.builds, [])

    def test_photo_download_failure_waits_for_next_check(self):
        draft_id = caption_approved(self.conn)
        with mock.patch.object(cli, "build_image", return_value=None):
            cli.cmd_images(CFG, self.conn)
        status, payload = self.row(draft_id)
        self.assertEqual(status, "approved")      # not published as text-only
        self.assertNotIn("image_versions", payload)
        self.assertEqual(self.replies, [])

    def test_reuses_ai_image_made_by_the_old_flow(self):
        import tempfile

        draft_id = caption_approved(self.conn)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as old:
            payload = self.row(draft_id)[1]
            payload.update(image_path=old.name, image_source="gemini")
            db.set_payload(self.conn, draft_id, payload)
            cli.cmd_images(CFG, self.conn)
        self.assertEqual(self.builds, [])                       # nothing generated
        self.assertEqual(db.image_calls_for(self.conn, draft_id), 0)
        self.assertEqual(self.cards[0]["source"], "gemini")
        self.assertEqual(self.cards[0]["tries"], 2)            # 🔄 still available

    def test_publish_uses_the_approved_image_card(self):
        draft_id = caption_approved(self.conn)
        cli.cmd_images(CFG, self.conn)
        self.decide("accept")
        with mock.patch.object(discord, "card_image_url", return_value="https://cdn/img.jpg") as url, \
                mock.patch.object(threads, "maybe_refresh", return_value=False), \
                mock.patch.object(threads, "publish", return_value={"id": "T", "permalink": "https://t/p"}):
            cli.cmd_publish(CFG, self.conn)
        url.assert_called_once_with("CH", "IMG1")
        self.assertEqual(self.row(draft_id)[0], "published")


class ImageDecisionTest(unittest.TestCase):
    def decide(self, taps):
        with mock.patch.object(discord, "owner_taps", return_value=set(taps)):
            return discord.read_image_decision("CH", "M", [])

    def test_priorities(self):
        self.assertIsNone(self.decide([]))
        self.assertEqual(self.decide(["✅"]), "accept")
        self.assertEqual(self.decide(["⬜"]), "plain")
        self.assertEqual(self.decide(["✅", "🔄"]), "retry")
        self.assertEqual(self.decide(["✅", "❌"]), "reject")

    def test_image_card_offers_only_available_actions(self):
        product = {"store": "dm", "brand": "B", "name": "N"}
        ai = discord.build_image_card(3, product, "gemini", 1, reply_to="CAP")["content"]
        self.assertIn("🔄", ai)
        self.assertIn("⬜", ai)
        plain = discord.build_image_card(3, product, "fallback", 0)["content"]
        self.assertNotIn("🔄", plain)
        self.assertNotIn("⬜", plain)


if __name__ == "__main__":
    unittest.main()
