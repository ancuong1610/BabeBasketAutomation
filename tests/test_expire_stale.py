"""Waiting drafts older than [discord] expire_after_days stop being polled."""

import json
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from babebasket import cli, db


def draft(conn, status, created_at, payload=None):
    draft_id = db.add_draft(conn, "dm", f"p{created_at}", payload or {"draft": {"captions": ["a"]}})
    conn.execute("UPDATE drafts SET status = ?, created_at = ? WHERE id = ?", (status, created_at, draft_id))
    conn.commit()
    return draft_id


class ExpireStaleTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.archive = self.enterContext(mock.patch.object(cli, "archive_draft_thread"))

    def status(self, draft_id):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def test_expires_old_waiting_drafts_only(self):
        old_caption = draft(self.conn, "pending", "2026-11-17T00:16:00+00:00")
        old_image = draft(self.conn, "image_pending", "2026-11-20T06:13:00+00:00", {"thread_id": "55", "draft": {}})
        old_ready = draft(self.conn, "ready", "2026-11-17T00:16:00+00:00")
        fresh = draft(self.conn, "pending", "2026-11-25T06:11:00+00:00")
        now = "2026-11-25T12:00:00+00:00"
        expired = cli.expire_stale({"discord": {"expire_after_days": 3}}, self.conn, now=now)
        self.assertEqual(expired, [old_caption, old_image])
        status, payload = self.status(old_caption)
        self.assertEqual(status, "expired")
        self.assertIn("no decision", payload["closed_reason"])
        self.assertEqual(self.status(old_image)[0], "expired")
        self.assertEqual(self.status(old_ready)[0], "ready")      # approved work is never dropped
        self.assertEqual(self.status(fresh)[0], "pending")
        threads = [call.args[0].get("thread_id") for call in self.archive.call_args_list]
        self.assertIn("55", threads)                               # its thread is archived (best effort)

    def test_disabled_without_setting(self):
        old = draft(self.conn, "pending", "2026-11-01T00:00:00+00:00")
        self.assertEqual(cli.expire_stale({"discord": {}}, self.conn, now="2026-11-25T12:00:00+00:00"), [])
        self.assertEqual(self.status(old)[0], "pending")

    def test_live_config_turns_it_on(self):
        live = tomllib.loads((Path(__file__).parents[1] / "config.toml").read_text())
        self.assertGreater(live["discord"].get("expire_after_days", 0), 0)


if __name__ == "__main__":
    unittest.main()
