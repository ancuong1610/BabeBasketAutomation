import json
import unittest
from unittest import mock

from babebasket import cli, db, discord, threads


class FakeApi:
    """Records Threads API calls; replies by path."""

    def __init__(self, statuses=("IN_PROGRESS", "FINISHED"), fail_publish=False):
        self.calls = []
        self.statuses = list(statuses)
        self.fail_publish = fail_publish

    def __call__(self, url, *, method="GET", retries=4, **kw):
        path = url.split("graph.threads.net/v1.0/")[1].split("?")[0]
        self.calls.append((method, path, url, retries))
        if path == "me":
            return {"id": "U1", "username": "tester"}
        if path == "U1/threads":
            return {"id": "C1"}
        if path == "C1":
            s = self.statuses.pop(0)
            return {"status": s, "error_message": "bad image" if s == "ERROR" else None}
        if path == "U1/threads_publish":
            if self.fail_publish:
                raise RuntimeError("HTTP 400 for POST")
            return {"id": "M1"}
        if path == "M1":
            return {"permalink": "https://www.threads.com/@tester/post/X"}
        raise AssertionError(path)


class PublishTest(unittest.TestCase):
    def run_publish(self, api, image_url="https://cdn.discordapp.com/a/post.jpg"):
        with mock.patch.object(threads, "request_json", side_effect=api), \
             mock.patch.object(threads, "token", return_value="TKN"), \
             mock.patch.object(threads.time, "sleep"):
            return threads.publish("Xin chào", image_url)

    def test_image_post_flow_in_order(self):
        api = FakeApi()
        out = self.run_publish(api)
        self.assertEqual(out, {"id": "M1", "permalink": "https://www.threads.com/@tester/post/X"})
        paths = [c[1] for c in api.calls]
        self.assertEqual(paths, ["me", "U1/threads", "C1", "C1", "U1/threads_publish", "M1"])
        create = api.calls[1][2]
        self.assertIn("media_type=IMAGE", create)
        self.assertIn("image_url=https%3A%2F%2Fcdn.discordapp.com", create)

    def test_text_post_when_no_image(self):
        api = FakeApi(statuses=("FINISHED",))
        self.run_publish(api, image_url=None)
        create = api.calls[1][2]
        self.assertIn("media_type=TEXT", create)
        self.assertNotIn("image_url", create)

    def test_container_error_raises_and_does_not_publish(self):
        api = FakeApi(statuses=("ERROR",))
        with self.assertRaisesRegex(RuntimeError, "bad image"):
            self.run_publish(api)
        self.assertNotIn("U1/threads_publish", [c[1] for c in api.calls])

    def test_publish_call_is_never_retried(self):
        api = FakeApi()
        self.run_publish(api)
        publish = [c for c in api.calls if c[1] == "U1/threads_publish"][0]
        self.assertEqual(publish[0], "POST")
        self.assertEqual(publish[3], 0)


def approved_draft(conn, caption_text="Caption hai 99🐠", chosen=2):
    payload = {"product": {"brand": "B", "name": "N"}, "quote": {}, "warnings": [],
               "draft": {"captions": [{"style": "a", "text": "Caption một"}, {"style": "b", "text": caption_text}]}}
    draft_id = db.add_draft(conn, "dm", "1", payload)
    db.set_status(conn, draft_id, "ready", discord_message_id="MSG", chosen_caption=chosen)
    return draft_id


CFG = {"threads": {"enabled": True}, "discord": {"channel_id": "CH"}}


class CmdPublishTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.replies = []
        self.patches = [
            mock.patch.object(discord, "reply", side_effect=lambda ch, mid, text: self.replies.append(text)),
            mock.patch.object(discord, "card_image_url", return_value="https://cdn/post.jpg"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def row(self, draft_id):
        r = self.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return r["status"], json.loads(r["payload"])

    def test_disabled_does_nothing(self):
        draft_id = approved_draft(self.conn)
        with mock.patch.object(threads, "publish") as pub:
            cli.cmd_publish({"threads": {"enabled": False}, "discord": {"channel_id": "CH"}}, self.conn)
        pub.assert_not_called()
        self.assertEqual(self.row(draft_id)[0], "ready")

    def test_success_marks_published_and_replies_with_link(self):
        draft_id = approved_draft(self.conn)
        with mock.patch.object(threads, "publish", return_value={"id": "M1", "permalink": "https://t/p"}) as pub:
            cli.cmd_publish(CFG, self.conn)
        pub.assert_called_once_with("Caption hai 99🐠", "https://cdn/post.jpg", extra_images=None)
        status, payload = self.row(draft_id)
        self.assertEqual(status, "published")
        self.assertEqual(payload["threads"]["permalink"], "https://t/p")
        self.assertIn("https://t/p", self.replies[0])

    def test_failure_counts_attempt_and_stays_ready(self):
        draft_id = approved_draft(self.conn)
        with mock.patch.object(threads, "publish", side_effect=RuntimeError("HTTP 500")):
            cli.cmd_publish(CFG, self.conn)
        status, payload = self.row(draft_id)
        self.assertEqual(status, "ready")
        self.assertEqual(payload["publish_attempts"], 1)
        self.assertIn("HTTP 500", self.replies[0])

    def test_third_failure_gives_up(self):
        draft_id = approved_draft(self.conn)
        with mock.patch.object(threads, "publish", side_effect=RuntimeError("HTTP 500")):
            for _ in range(3):
                cli.cmd_publish(CFG, self.conn)
        self.assertEqual(self.row(draft_id)[0], "publish_failed")

    def test_long_caption_fails_without_calling_api(self):
        draft_id = approved_draft(self.conn, caption_text="x" * 501)
        with mock.patch.object(threads, "publish") as pub:
            cli.cmd_publish(CFG, self.conn)
        pub.assert_not_called()
        self.assertEqual(self.row(draft_id)[0], "publish_failed")
        self.assertIn("500", self.replies[0])


class CardImageUrlTest(unittest.TestCase):
    def test_prefers_attachment_then_embed_image(self):
        with mock.patch.object(discord, "_call", return_value={"attachments": [{"url": "https://a/post.jpg?ex=1"}],
                                                               "embeds": [{"image": {"url": "https://e"}}]}):
            self.assertEqual(discord.card_image_url("CH", "M"), "https://a/post.jpg?ex=1")
        with mock.patch.object(discord, "_call", return_value={"attachments": [], "embeds": [{"image": {"url": "https://e"}}]}):
            self.assertEqual(discord.card_image_url("CH", "M"), "https://e")
        with mock.patch.object(discord, "_call", return_value={"attachments": [], "embeds": [{}]}):
            self.assertIsNone(discord.card_image_url("CH", "M"))


if __name__ == "__main__":
    unittest.main()


class TokenRefreshTest(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self.tmp = tempfile.TemporaryDirectory()
        self.file = pathlib.Path(self.tmp.name) / "threads_token.json"
        self.p = [mock.patch.object(threads, "TOKEN_FILE", self.file),
                  mock.patch.dict(threads.os.environ, {"THREADS_ACCESS_TOKEN": "ENV1"})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.tmp.cleanup()

    def test_first_run_records_env_token_without_calling(self):
        with mock.patch.object(threads, "request_json") as rj:
            self.assertFalse(threads.maybe_refresh())
        rj.assert_not_called()
        self.assertEqual(threads.token(), "ENV1")

    def test_week_old_token_is_refreshed_and_used(self):
        threads.maybe_refresh()
        saved = json.loads(self.file.read_text())
        saved["refreshed_at"] = "2020-01-01T00:00:00+00:00"
        self.file.write_text(json.dumps(saved))
        with mock.patch.object(threads, "request_json", return_value={"access_token": "NEW", "expires_in": 5184000}):
            self.assertTrue(threads.maybe_refresh())
        self.assertEqual(threads.token(), "NEW")

    def test_new_env_token_wins_over_saved_one(self):
        threads.maybe_refresh()
        saved = json.loads(self.file.read_text())
        saved["access_token"] = "REFRESHED_OLD"
        self.file.write_text(json.dumps(saved))
        with mock.patch.dict(threads.os.environ, {"THREADS_ACCESS_TOKEN": "ENV2"}):
            self.assertEqual(threads.token(), "ENV2")


class CaptionLengthTest(unittest.TestCase):
    def test_caption_over_threads_limit_is_flagged(self):
        from babebasket import llm
        draft = {"captions": [{"style": "product highlights", "text": "{PRICE} " + "x" * 520},
                              {"style": "product highlights", "text": "{PRICE} ngắn"}],
                 "labels": {"title": "T", "mode": "usage", "points": ["a", "b"]}}
        w = llm.warnings(draft)
        self.assertIn("caption 1 is too long for Threads (528/500)", w)
        self.assertFalse(any("caption 2 is too long" in x for x in w))

    def test_prompt_asks_for_short_captions(self):
        from babebasket import llm
        self.assertIn("tối đa 450 ký tự", llm.USER_TEMPLATE)
