import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

from babebasket import cli, db, discord, meta, threads
from tests.test_threads import approved_draft

IMAGE = "https://cdn/post.jpg?ex=123"
EXTRA = "https://shop/swatch.jpg"
CAPTION = "Ảnh mới 🌸"


class MetaRequestTest(unittest.TestCase):
    def setUp(self):
        secrets = {"INSTAGRAM_USER_ID": "IG", "FACEBOOK_PAGE_ID": "FB", "META_PAGE_ACCESS_TOKEN": "dummy+/="}
        self.enterContext(mock.patch.object(meta.config, "secret", side_effect=secrets.__getitem__))
        self.sleep = self.enterContext(mock.patch.object(meta.time, "sleep"))
        self.api = self.enterContext(mock.patch.object(meta, "request_json"))
        # A real INSTAGRAM_ACCESS_TOKEN in .env must not switch these tests to Instagram Login.
        self.enterContext(mock.patch.object(meta.config, "load_dotenv"))
        env = {k: v for k, v in meta.os.environ.items() if k != "INSTAGRAM_ACCESS_TOKEN"}
        self.enterContext(mock.patch.dict(meta.os.environ, env, clear=True))

    def query_calls(self, version="v21.0"):
        calls = []
        for call in self.api.call_args_list:
            url = urlsplit(call.args[0])
            self.assertEqual((url.scheme, url.netloc), ("https", "graph.facebook.com"))
            self.assertTrue(url.path.startswith(f"/{version}/"))
            params = dict(parse_qsl(url.query))
            self.assertEqual(params.pop("access_token"), "dummy+/=")
            self.assertNotIn("body", call.kwargs)
            self.assertNotIn("headers", call.kwargs)
            calls.append((call.kwargs["method"], url.path.removeprefix(f"/{version}/"), params))
        return calls

    def test_instagram_single_polls_and_publishes_once(self):
        self.api.side_effect = [{"id": "C"}, {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"},
                                {"id": "M"}, {"permalink": "https://ig/p"}]
        self.assertEqual(meta.instagram_publish(CAPTION, IMAGE), {"id": "M", "permalink": "https://ig/p"})
        self.assertEqual(self.query_calls(), [
            ("POST", "IG/media", {"image_url": IMAGE, "caption": CAPTION}),
            ("GET", "C", {"fields": "status_code"}),
            ("GET", "C", {"fields": "status_code"}),
            ("POST", "IG/media_publish", {"creation_id": "C"}),
            ("GET", "M", {"fields": "permalink"}),
        ])
        self.sleep.assert_called_once_with(5)
        self.assertEqual(self.api.call_args_list[3].kwargs["retries"], 0)

    def test_instagram_login_token_uses_graph_instagram_without_a_page(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGown"
        self.api.side_effect = [{"id": "C"}, {"status_code": "FINISHED"}, {"id": "M"}, {"permalink": "https://ig/p"}]
        meta.instagram_publish(CAPTION, IMAGE)
        for call in self.api.call_args_list:
            url = urlsplit(call.args[0])
            self.assertEqual(url.netloc, "graph.instagram.com")
            self.assertEqual(dict(parse_qsl(url.query))["access_token"], "IGown")

    def test_facebook_type_instagram_token_uses_graph_facebook(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "EAAown"
        self.api.side_effect = [{"id": "C"}, {"status_code": "FINISHED"}, {"id": "M"}, {"permalink": "https://ig/p"}]
        meta.instagram_publish(CAPTION, IMAGE)
        for call in self.api.call_args_list:
            url = urlsplit(call.args[0])
            self.assertEqual(url.netloc, "graph.facebook.com")
            self.assertEqual(dict(parse_qsl(url.query))["access_token"], "EAAown")

    def test_instagram_carousel_preserves_order_and_uses_configured_version(self):
        self.api.side_effect = [{"id": "C1"}, {"id": "C2"}, {"id": "C"}, {"status_code": "FINISHED"},
                                {"id": "M"}, {"permalink": ""}]
        meta.instagram_publish(CAPTION, IMAGE, [EXTRA], graph_version="v22.0")
        self.assertEqual(self.query_calls("v22.0")[:3], [
            ("POST", "IG/media", {"image_url": IMAGE, "is_carousel_item": "true"}),
            ("POST", "IG/media", {"image_url": EXTRA, "is_carousel_item": "true"}),
            ("POST", "IG/media", {"media_type": "CAROUSEL", "children": "C1,C2", "caption": CAPTION}),
        ])

    def test_instagram_validates_image_and_caption_before_requesting(self):
        with self.assertRaisesRegex(ValueError, "^Instagram needs an image$"):
            meta.instagram_publish(CAPTION, None)
        with self.assertRaisesRegex(ValueError, "2200"):
            meta.instagram_publish("a" * 2201, IMAGE)
        self.api.assert_not_called()

    def test_instagram_error_expiry_and_timeout_prevent_publication(self):
        for status, reason in [("ERROR", "ERROR"), ("EXPIRED", "EXPIRED"), ("IN_PROGRESS", "not ready")]:
            with self.subTest(status=status):
                self.api.reset_mock(side_effect=True)
                self.api.side_effect = [{"id": "C"}, {"status_code": status}]
                with self.assertRaisesRegex(RuntimeError, reason):
                    meta.instagram_publish(CAPTION, IMAGE, wait_s=0)
                self.assertEqual(self.api.call_count, 2)

    def test_instagram_permalink_failure_preserves_success_and_hides_token(self):
        self.api.side_effect = [{"id": "C"}, {"status_code": "FINISHED"}, {"id": "M"},
                                RuntimeError("unexpected token dummy+/=")]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(meta.instagram_publish(CAPTION, IMAGE), {"id": "M", "permalink": ""})
        self.assertIn("Instagram permalink lookup failed", stderr.getvalue())
        self.assertNotIn("dummy", stderr.getvalue())

    def test_facebook_single_photo_uses_post_id_or_falls_back(self):
        for photo, post_id in [({"id": "P", "post_id": "F"}, "F"), ({"id": "P"}, "P")]:
            with self.subTest(photo=photo):
                self.api.reset_mock(side_effect=True)
                self.api.side_effect = [photo, {"permalink_url": "https://fb/p"}]
                self.assertEqual(meta.facebook_publish(CAPTION, IMAGE), {"id": post_id, "permalink": "https://fb/p"})
                self.assertEqual(self.query_calls(), [
                    ("POST", "FB/photos", {"url": IMAGE, "caption": CAPTION}),
                    ("GET", post_id, {"fields": "permalink_url"}),
                ])
                self.assertEqual(self.api.call_args_list[0].kwargs["retries"], 0)

    def test_facebook_uploads_unpublished_photos_then_attaches_them_to_feed(self):
        self.api.side_effect = [{"id": "P1"}, {"id": "P2"}, {"id": "F"}, {"permalink_url": "https://fb/p"}]
        meta.facebook_publish(CAPTION, IMAGE, [EXTRA])
        self.assertEqual(self.query_calls(), [
            ("POST", "FB/photos", {"url": IMAGE, "published": "false"}),
            ("POST", "FB/photos", {"url": EXTRA, "published": "false"}),
            ("POST", "FB/feed", {"message": CAPTION, "attached_media[0]": '{"media_fbid":"P1"}',
                                 "attached_media[1]": '{"media_fbid":"P2"}'}),
            ("GET", "F", {"fields": "permalink_url"}),
        ])
        self.assertEqual(self.api.call_args_list[2].kwargs["retries"], 0)

    def test_facebook_text_only_posts_to_feed_without_retrying(self):
        self.api.side_effect = [{"id": "F"}, RuntimeError("lookup error")]
        with redirect_stderr(io.StringIO()):
            self.assertEqual(meta.facebook_publish(CAPTION), {"id": "F", "permalink": ""})
        self.assertEqual(self.query_calls()[0], ("POST", "FB/feed", {"message": CAPTION}))
        self.assertEqual(self.api.call_args_list[0].kwargs["retries"], 0)

    def test_long_facebook_caption_uses_form_fields_and_keeps_token_in_query(self):
        message = "à" * 60000
        self.api.return_value = {"permalink_url": "https://fb/p"}
        with mock.patch.object(meta, "request_multipart", return_value={"id": "F"}) as form:
            self.assertEqual(meta.facebook_publish(message), {"id": "F", "permalink": "https://fb/p"})
        form.assert_called_once_with("https://graph.facebook.com/v21.0/FB/feed?access_token=dummy%2B%2F%3D",
                                     fields={"message": message}, files=[], retries=0, timeout=60)


class MultiPlatformPublishTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.cfg = {"discord": {"channel_id": "CH"}, "threads": {"enabled": True},
                    "instagram": {"enabled": True}, "facebook": {"enabled": True}}
        self.calls = mock.Mock()
        for name, module, function in [("threads", threads, "publish"), ("instagram", meta, "instagram_publish"),
                                       ("facebook", meta, "facebook_publish")]:
            publish = self.enterContext(mock.patch.object(module, function,
                                        return_value={"id": name, "permalink": f"https://{name}/p"}))
            self.calls.attach_mock(publish, name)
        self.refresh = self.enterContext(mock.patch.object(threads, "maybe_refresh", return_value=False))
        self.image = self.enterContext(mock.patch.object(discord, "card_image_url", return_value=IMAGE))
        self.reply = self.enterContext(mock.patch.object(discord, "reply"))

    def row(self, draft_id):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def test_posts_to_each_platform_in_order_with_the_same_images(self):
        draft_id = approved_draft(self.conn, CAPTION)
        _, payload = self.row(draft_id)
        db.set_payload(self.conn, draft_id, {**payload, "image_message_id": "IMG", "supporting": [EXTRA]})
        cli.cmd_publish(self.cfg, self.conn)
        cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(self.calls.mock_calls, [mock.call.threads(CAPTION, IMAGE, extra_images=[EXTRA]),
                                                mock.call.instagram(CAPTION, IMAGE, extra_images=[EXTRA]),
                                                mock.call.facebook(CAPTION, IMAGE, extra_images=[EXTRA])])
        self.image.assert_called_once_with("CH", "IMG")
        status, payload = self.row(draft_id)
        self.assertEqual(status, "published")
        for name, emoji in [("threads", "🧵"), ("instagram", "📸"), ("facebook", "📘")]:
            self.assertEqual(payload["posted"][name]["id"], name)
            self.assertTrue(payload["posted"][name]["published_at"])
            self.assertTrue(any(call.args[2].startswith(emoji) and f"https://{name}/p" in call.args[2]
                                for call in self.reply.call_args_list))

    def test_retry_only_the_failed_platform_then_mark_published(self):
        draft_id = approved_draft(self.conn, CAPTION)
        self.calls.instagram.side_effect = [RuntimeError("temporary"), {"id": "ig", "permalink": ""}]
        cli.cmd_publish(self.cfg, self.conn)
        status, payload = self.row(draft_id)
        self.assertEqual(status, "ready")
        self.assertEqual(payload["publish_attempts"], {"instagram": 1})
        self.assertEqual(set(payload["posted"]), {"threads", "facebook"})
        cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(self.row(draft_id)[0], "published")
        self.assertEqual([call[0] for call in self.calls.mock_calls], ["threads", "instagram", "facebook", "instagram"])

    def test_exhausted_platforms_become_terminal_and_each_announces_once(self):
        for facebook_succeeds in [False, True]:
            with self.subTest(facebook_succeeds=facebook_succeeds):
                self.calls.reset_mock()
                self.reply.reset_mock()
                self.calls.threads.side_effect = self.calls.instagram.side_effect = RuntimeError("down")
                self.calls.facebook.side_effect = None if facebook_succeeds else RuntimeError("down")
                draft_id = approved_draft(self.conn, CAPTION)
                for _ in range(cli.MAX_PUBLISH_ATTEMPTS + 1):
                    cli.cmd_publish(self.cfg, self.conn)
                status, payload = self.row(draft_id)
                gave_up = payload["gave_up"]
                self.assertEqual(gave_up, ["threads", "instagram"] if facebook_succeeds else
                                 ["threads", "instagram", "facebook"])
                self.assertEqual(status, "published" if facebook_succeeds else "publish_failed")
                final_notes = [call.args[2] for call in self.reply.call_args_list if "dừng đăng" in call.args[2]]
                self.assertEqual(len(final_notes), len(gave_up))
                self.assertEqual(payload["publish_attempts"], dict.fromkeys(gave_up, cli.MAX_PUBLISH_ATTEMPTS))

    def test_migrates_legacy_threads_post_and_integer_attempts(self):
        draft_id = approved_draft(self.conn, CAPTION)
        _, payload = self.row(draft_id)
        legacy = {"id": "older", "permalink": "https://threads/older", "published_at": "2026-11-21"}
        db.set_payload(self.conn, draft_id, {**payload, "threads": legacy, "publish_attempts": 2})
        cli.cmd_publish(self.cfg, self.conn)
        self.calls.threads.assert_not_called()
        status, payload = self.row(draft_id)
        self.assertEqual(status, "published")
        self.assertEqual(payload["posted"]["threads"], legacy)
        self.assertEqual(payload["publish_attempts"], {"threads": 2})

    def test_disabled_platforms_are_skipped_and_custom_version_is_used(self):
        draft_id = approved_draft(self.conn, CAPTION)
        self.cfg.update(threads={"enabled": False}, instagram={"enabled": False}, meta={"graph_version": "v22.0"})
        cli.cmd_publish(self.cfg, self.conn)
        self.refresh.assert_not_called()
        self.assertEqual(self.calls.mock_calls, [mock.call.facebook(CAPTION, IMAGE, extra_images=None,
                                                                  graph_version="v22.0")])
        self.assertEqual(self.row(draft_id)[0], "published")

    def test_length_limits_apply_only_to_the_platforms_the_caption_exceeds(self):
        for length, gave_up in [(501, ["threads"]), (2201, ["threads", "instagram"]),
                                (60001, ["threads", "instagram", "facebook"])]:
            with self.subTest(length=length):
                draft_id = approved_draft(self.conn, "x" * length)
                cli.cmd_publish(self.cfg, self.conn)
                status, payload = self.row(draft_id)
                self.assertEqual(payload["gave_up"], gave_up)
                self.assertEqual(set(payload["posted"]), set(["threads", "instagram", "facebook"]) - set(gave_up))
                self.assertEqual(status, "published" if payload["posted"] else "publish_failed")

    def test_manual_publish_cannot_duplicate_a_running_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            first, second = db.connect(path), db.connect(path)
            try:
                approved_draft(first, CAPTION)

                def publish_threads(*args, **kwargs):
                    cli.cmd_publish(self.cfg, second)
                    return {"id": "threads", "permalink": ""}

                self.calls.threads.side_effect = publish_threads
                cli.cmd_publish(self.cfg, first)
                self.assertEqual([call[0] for call in self.calls.mock_calls], ["threads", "instagram", "facebook"])
            finally:
                first.close()
                second.close()


class InstagramTokenRefreshTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.enterContext(mock.patch.object(meta, "TOKEN_FILE", tmp / "instagram_token.json"))
        self.enterContext(mock.patch.object(meta.config, "load_dotenv"))
        env = {k: v for k, v in meta.os.environ.items()
               if k not in ("INSTAGRAM_ACCESS_TOKEN", "META_APP_ID", "META_APP_SECRET")}
        self.enterContext(mock.patch.dict(meta.os.environ, env, clear=True))
        self.api = self.enterContext(mock.patch.object(meta, "request_json"))

    def _age(self, days):
        data = json.loads(meta.TOKEN_FILE.read_text())
        data["refreshed_at"] = (meta.dt.datetime.now(meta.dt.timezone.utc) - meta.dt.timedelta(days=days)).isoformat()
        meta.TOKEN_FILE.write_text(json.dumps(data))

    def test_first_run_starts_the_clock_without_a_call(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGold"
        self.assertFalse(meta.maybe_refresh_instagram())
        self.api.assert_not_called()
        self.assertEqual(meta.instagram_token(), "IGold")

    def test_ig_token_refreshes_after_a_week(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGold"
        meta.maybe_refresh_instagram()
        self._age(8)
        self.api.return_value = {"access_token": "IGnew", "expires_in": 5184000}
        self.assertTrue(meta.maybe_refresh_instagram())
        url = self.api.call_args.args[0]
        self.assertTrue(url.startswith("https://graph.instagram.com/refresh_access_token?"))
        self.assertIn("ig_refresh_token", url)
        self.assertEqual(meta.instagram_token(), "IGnew")
        self.assertEqual(meta.TOKEN_FILE.stat().st_mode & 0o777, 0o600)

    def test_eaa_token_needs_app_secret_then_exchanges(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "EAAold"
        meta.maybe_refresh_instagram()
        self._age(8)
        with self.assertRaisesRegex(RuntimeError, "META_APP_SECRET"):
            meta.maybe_refresh_instagram()
        meta.os.environ.update({"META_APP_ID": "1", "META_APP_SECRET": "s3cret"})
        self.api.side_effect = [{"data": {"is_valid": True, "expires_at": 1999999999}},
                                {"access_token": "EAAnew", "expires_in": 5184000}]
        self.assertTrue(meta.maybe_refresh_instagram())
        self.assertIn("fb_exchange_token", self.api.call_args.args[0])
        self.assertEqual(meta.instagram_token(), "EAAnew")

    def test_exchange_error_hides_the_secret(self):
        meta.os.environ.update({"INSTAGRAM_ACCESS_TOKEN": "EAAold", "META_APP_ID": "1", "META_APP_SECRET": "s3cret"})
        meta.maybe_refresh_instagram()
        self._age(8)
        self.api.side_effect = [{"data": {"is_valid": True, "expires_at": 1999999999}},
                                RuntimeError("HTTP 400 for https://graph.facebook.com/...client_secret=s3cret")]
        with self.assertRaises(RuntimeError) as ctx:
            meta.maybe_refresh_instagram()
        self.assertNotIn("s3cret", str(ctx.exception))

    def test_new_env_token_wins_over_the_saved_one(self):
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGold"
        meta.maybe_refresh_instagram()
        meta.os.environ["INSTAGRAM_ACCESS_TOKEN"] = "IGpasted"
        self.assertEqual(meta.instagram_token(), "IGpasted")

    def test_system_user_token_that_never_expires_is_left_alone(self):
        meta.os.environ.update({"INSTAGRAM_ACCESS_TOKEN": "EAAsys", "META_APP_ID": "1", "META_APP_SECRET": "s3cret"})
        meta.maybe_refresh_instagram()
        self._age(8)
        self.api.return_value = {"data": {"is_valid": True, "expires_at": 0}}
        self.assertFalse(meta.maybe_refresh_instagram())
        self.assertEqual(self.api.call_count, 1)  # only the debug_token check, no exchange
        self._age(30)
        self.assertFalse(meta.maybe_refresh_instagram())
        self.assertEqual(self.api.call_count, 1)  # remembered: never checked again
        self.assertEqual(meta.instagram_token(), "EAAsys")
