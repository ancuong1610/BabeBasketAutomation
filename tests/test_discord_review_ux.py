"""Discord image review and publish confirmations with a fake Discord REST API."""

import json
import unittest
import urllib.parse
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from babebasket import cli, db, discord, meta, threads
from tests.test_gift_photos import png_rgba
from tests.test_image_flow import caption_approved


MAIN, POSTED = "100", "900"
IMAGE_URL = "https://cdn.example/post.jpg"
CFG = {"discord": {"channel_id": MAIN, "owner_user_ids": ["42"]},
       "image": {"max_per_post": 3, "max_per_day": 10}}


class ThreadReviewTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.data = Path(self.enterContext(TemporaryDirectory()))
        self.incoming = defaultdict(list)
        self.cards = {}
        self.next_message = 1000
        self.fail_create = self.fail_marker = self.fail_archive = False
        self.enterContext(mock.patch.object(cli, "DATA", self.data))
        self.enterContext(mock.patch.object(cli, "fetch_bytes", return_value=png_rgba()))
        self.api = self.enterContext(mock.patch.object(discord, "_call", side_effect=self.discord_api))
        self.upload = self.enterContext(mock.patch.object(discord, "_upload", side_effect=self.upload_card))
        self.enterContext(mock.patch.object(discord.time, "sleep"))
        self.build = self.enterContext(mock.patch.object(cli, "build_image", side_effect=self.build_image))

    def discord_api(self, method, path, body=None):
        _, _, channel, *rest = path.split("/")
        if rest and rest[-1] == "threads":
            if self.fail_create:
                raise RuntimeError("missing Create Public Threads permission")
            return {"id": rest[1]}
        if method == "PATCH" and not rest and self.fail_archive:
            raise RuntimeError("cannot archive")
        if method == "PATCH" and rest:
            if self.fail_marker:
                raise RuntimeError("could not edit old card")
            self.cards[channel, rest[1]].update(body)
        if method == "POST":
            self.next_message += 1
            return {"id": str(self.next_message)}
        if method == "GET" and "?after=" in path:
            after = int(path.split("?after=")[1].split("&")[0])
            return [message for message in self.incoming[channel] if int(message["id"]) > after]
        if method == "GET":
            return self.cards[channel, rest[1]]

    def upload_card(self, path, card, files, method="POST"):
        _, _, channel, _, *rest = path.split("/")
        self.next_message += 1
        message_id = rest[0] if method == "PATCH" else str(self.next_message)
        self.cards[channel, message_id] = card
        return {"id": message_id}

    def build_image(self, payload, out, cfg, **kwargs):
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(b"fake hero")
        payload.update(image_path=str(out), image_source="gemini")
        return str(out)

    def approved(self, caption="201"):
        draft_id = caption_approved(self.conn, product_id=caption)
        db.set_status(self.conn, draft_id, "approved", discord_message_id=caption)
        return draft_id

    def state(self, draft_id):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def owner_message(self, channel, content="", **fields):
        self.next_message += 1
        self.incoming[channel].append({"id": str(self.next_message), "author": {"id": "42"},
                                       "content": content, **fields})

    def test_first_image_starts_thread_and_posts_card_and_picker_there(self):
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        status, payload = self.state(draft_id)
        self.assertEqual(status, "image_pending")
        self.api.assert_any_call("POST", "/channels/100/messages/201/threads",
                                 {"name": "#1 · B N", "auto_archive_duration": 10080})
        self.assertEqual(payload["thread_id"], "201")
        image_card = self.cards["201", payload["image_message_id"]]
        self.assertNotIn("message_reference", image_card)
        for phrase in ("ảnh v1", "✅ đăng", "❌ bỏ", "gõ vào thread", "🔤", "📷 gửi ảnh"):
            self.assertIn(phrase, image_card["content"])
        self.assertNotIn("đã sửa", image_card["content"])
        self.assertIn(("201", payload["picker"]["message_id"]), self.cards)
        # Owner 2026-11-24: no ✏️ in threads, typing is the edit.
        reacted = self.reactions("201", payload["image_message_id"])
        self.assertEqual(reacted, {discord.APPROVE, discord.REJECT, discord.TEXT})
        self.assertNotIn("✏️", image_card["content"])

    def reactions(self, channel, message_id):
        prefix = f"/channels/{channel}/messages/{message_id}/reactions/"
        return {urllib.parse.unquote(c.args[1][len(prefix):].split("/")[0])
                for c in self.api.call_args_list if c.args[0] == "PUT" and c.args[1].startswith(prefix)}

    def test_thread_creation_failure_keeps_card_in_main_channel(self):
        self.fail_create = True
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        _, payload = self.state(draft_id)
        self.api.assert_any_call("POST", "/channels/100/messages/201/threads",
                                 {"name": "#1 · B N", "auto_archive_duration": 10080})
        self.assertNotIn("thread_id", payload)
        card = self.cards[MAIN, payload["image_message_id"]]
        self.assertEqual(card["message_reference"]["message_id"], "201")
        self.assertIn("reply", card["content"])
        self.assertIn(discord.EDIT, self.reactions(MAIN, payload["image_message_id"]))  # legacy flow unchanged

    def test_plain_owner_edits_are_isolated_by_thread(self):
        ids = [self.approved(channel) for channel in ("201", "202")]
        cli.cmd_images(CFG, self.conn)
        self.owner_message("201", "sản phẩm to hơn")
        self.owner_message("202", "nền sáng hơn")
        self.owner_message(MAIN, "unrelated")
        self.owner_message("201", "other user", author={"id": "99"})
        cli.cmd_check_images(CFG, self.conn)
        self.assertEqual([call.kwargs["feedback"] for call in self.build.call_args_list[2:]],
                         ["sản phẩm to hơn", "nền sáng hơn"])
        for draft_id, channel, feedback in zip(ids, ("201", "202"), ("sản phẩm to hơn", "nền sáng hơn")):
            _, payload = self.state(draft_id)
            content = self.cards[channel, payload["image_message_id"]]["content"]
            self.assertIn(f"v2 (đã sửa: {feedback})", content)

    def test_existing_legacy_draft_keeps_main_channel_reply_flow(self):
        self.fail_create = True
        legacy = self.approved()
        cli.cmd_images(CFG, self.conn)
        self.fail_create = False
        threaded = self.approved("202")
        cli.cmd_images(CFG, self.conn)
        legacy_card = self.state(legacy)[1]["image_message_id"]
        self.owner_message(MAIN, "unrelated")
        self.owner_message(MAIN, "to hơn", message_reference={"message_id": legacy_card})
        self.api.reset_mock()
        cli.cmd_check_images(CFG, self.conn)
        _, payload = self.state(legacy)
        self.assertEqual(self.state(threaded)[1]["thread_id"], "202")
        self.assertNotIn("thread_id", payload)
        self.assertIn((MAIN, payload["image_message_id"]), self.cards)
        self.assertEqual(self.build.call_args.kwargs["feedback"], "to hơn")
        self.assertFalse(any(call.args[1].endswith("/threads") for call in self.api.call_args_list))

    def test_image_upload_retry_reuses_saved_thread(self):
        draft_id = self.approved()
        self.upload.side_effect = RuntimeError("could not upload image")
        cli.cmd_images(CFG, self.conn)
        self.assertEqual(self.state(draft_id)[1]["thread_id"], "201")
        self.upload.side_effect = self.upload_card
        cli.cmd_images(CFG, self.conn)
        self.build.assert_called_once()
        self.assertEqual(sum(call.args[1].endswith("/threads") for call in self.api.call_args_list), 1)
        self.assertIn(("201", self.state(draft_id)[1]["image_message_id"]), self.cards)

    def test_replacement_marks_old_card_even_if_previous_marker_failed(self):
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        first = self.state(draft_id)[1]["image_message_id"]
        self.owner_message("201", "sản phẩm to hơn")
        cli.cmd_check_images(CFG, self.conn)
        self.assertTrue(self.cards["201", first]["content"].startswith("(cũ — xem v2)"))
        second = self.state(draft_id)[1]["image_message_id"]
        self.fail_marker = True
        self.owner_message("201", "nền sáng hơn")
        cli.cmd_check_images(CFG, self.conn)
        self.assertNotEqual(self.state(draft_id)[1]["image_message_id"], second)
        self.assertEqual(self.state(draft_id)[1]["image_versions"], 3)

    def test_owner_feedback_sent_during_an_edit_is_read_next_check(self):
        self.approved()
        cli.cmd_images(CFG, self.conn)
        self.owner_message("201", "sản phẩm to hơn")

        def build_with_followup(payload, out, cfg, **kwargs):
            self.owner_message("201", "nền sáng hơn")
            return self.build_image(payload, out, cfg, **kwargs)

        self.build.side_effect = build_with_followup
        cli.cmd_check_images(CFG, self.conn)
        self.build.side_effect = self.build_image
        cli.cmd_check_images(CFG, self.conn)
        self.assertEqual([call.kwargs["feedback"] for call in self.build.call_args_list[1:]],
                         ["sản phẩm to hơn", "nền sáng hơn"])

    def test_photo_without_prompt_uses_existing_picker_selection(self):
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        self.owner_message("201", attachments=[{"url": "https://cdn/owner.jpg", "filename": "owner.jpg"}])
        cli.cmd_check_images(CFG, self.conn)
        _, payload = self.state(draft_id)
        self.assertEqual(payload["picker"]["selected"], [2])
        self.assertTrue(payload["supporting"][0].startswith("file://"))
        self.assertIn("ảnh v1\n", self.cards["201", payload["image_message_id"]]["content"])
        self.build.assert_called_once()

    def test_picker_prompt_reads_only_its_thread_and_ignores_photo_caption_as_edit(self):
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        with mock.patch.object(discord, "owner_taps", return_value={discord.PHOTO}) as taps:
            cli.cmd_check_images(CFG, self.conn)
        self.assertTrue(all(call.args[0] == "201" for call in taps.call_args_list))
        self.assertTrue(self.state(draft_id)[1]["picker"]["photo_prompt"])
        self.owner_message(MAIN, attachments=[{"url": "https://cdn/main.jpg", "filename": "main.jpg"}])
        self.owner_message("201", "ảnh của mình", attachments=[{"url": "https://cdn/thread.jpg",
                                                                "filename": "thread.jpg"}])
        cli.cmd_check_images(CFG, self.conn)
        _, payload = self.state(draft_id)
        self.assertTrue(payload["picker"]["photo_received"])
        self.assertEqual(payload["picker"]["photo_message"], self.incoming["201"][0]["id"])
        self.build.assert_called_once()

    def test_reject_archives_thread_even_when_archive_fails(self):
        draft_id = self.approved()
        cli.cmd_images(CFG, self.conn)
        self.fail_archive = True
        with mock.patch.object(discord, "image_taps", return_value={discord.REJECT}):
            cli.cmd_check_images(CFG, self.conn)
        self.api.assert_any_call("PATCH", "/channels/201", {"archived": True})
        self.assertEqual(self.state(draft_id)[0], "rejected")


class PublishConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.cfg = {**CFG, "discord": {**CFG["discord"], "posted_channel_id": POSTED},
                    **{name: {"enabled": True} for name in ("threads", "instagram", "facebook")}}
        self.id = caption_approved(self.conn)
        _, payload = self.state()
        payload.update(thread_id="201", image_message_id="1001", image_versions=2)
        db.set_status(self.conn, self.id, "ready", discord_message_id="201", payload=json.dumps(payload))
        for platform, module, function in [("threads", threads, "publish"),
                                           ("instagram", meta, "instagram_publish"),
                                           ("facebook", meta, "facebook_publish")]:
            publish = self.enterContext(mock.patch.object(module, function,
                                        return_value={"id": platform, "permalink": f"https://{platform}.example/p"}))
            setattr(self, platform, publish)
        self.enterContext(mock.patch.object(threads, "maybe_refresh", return_value=False))
        self.enterContext(mock.patch.object(meta, "maybe_refresh_instagram", return_value=False))
        self.image = self.enterContext(mock.patch.object(discord, "card_image_url", return_value=IMAGE_URL))
        self.api = self.enterContext(mock.patch.object(discord, "_call", return_value={"id": "999"}))

    def state(self):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (self.id,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def messages(self, channel):
        return [call.args[2] for call in self.api.call_args_list
                if call.args[:2] == ("POST", f"/channels/{channel}/messages")]

    def test_success_sends_one_summary_main_reply_and_archive(self):
        cli.cmd_publish(self.cfg, self.conn)
        cli.cmd_publish(self.cfg, self.conn)
        status, payload = self.state()
        self.assertEqual((status, payload.get("posted_message_id")), ("published", "999"))
        self.image.assert_called_once_with("201", "1001")
        summary, = self.messages(POSTED)
        embed, = summary["embeds"]
        self.assertEqual(embed["title"], "B · N")
        self.assertIn(IMAGE_URL, [embed.get(key, {}).get("url") for key in ("image", "thumbnail")])
        self.assertEqual(embed["description"].splitlines(), [
            "Caption: 1", "Ảnh: v2", "✅ Threads https://threads.example/p",
            "✅ Instagram https://instagram.example/p", "✅ Facebook https://facebook.example/p", "Thread: <#201>"])
        caption_reply, = self.messages(MAIN)
        self.assertEqual(caption_reply["content"], "✅ Đã đăng — xem <#900>")
        self.assertEqual(caption_reply["message_reference"]["message_id"], "201")
        self.assertEqual([message["message_reference"]["message_id"] for message in self.messages("201")], ["1001"] * 3)
        self.assertEqual(self.api.call_args_list[-1], mock.call("PATCH", "/channels/201", {"archived": True}))

    def test_permanent_partial_failure_shows_successes_and_bounded_reason(self):
        self.facebook.side_effect = RuntimeError("Facebook unavailable " + "x" * 200)
        for _ in range(cli.MAX_PUBLISH_ATTEMPTS - 1):
            cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(self.messages(POSTED), [])
        cli.cmd_publish(self.cfg, self.conn)
        cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(self.state()[0], "publish_failed")
        summary, = self.messages(POSTED)
        lines = summary["embeds"][0]["description"].splitlines()
        self.assertIn("✅ Threads https://threads.example/p", lines)
        self.assertIn("✅ Instagram https://instagram.example/p", lines)
        self.assertIn(f"❌ Facebook: {str(self.facebook.side_effect)[:150]}", lines)
        caption_reply, = self.messages(MAIN)
        self.assertEqual(caption_reply["content"], "⚠️ Có nền tảng lỗi — xem <#900>")
        self.threads.assert_called_once()
        self.instagram.assert_called_once()

    def test_missing_or_empty_posted_channel_still_publishes(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.cfg["discord"].pop("posted_channel_id", None)
                if value is not None:
                    self.cfg["discord"]["posted_channel_id"] = value
                _, payload = self.state()
                payload.pop("posted", None)
                db.set_status(self.conn, self.id, "ready", payload=json.dumps(payload))
                cli.cmd_publish(self.cfg, self.conn)
                status, payload = self.state()
                self.assertEqual(status, "published")
                self.assertEqual(set(payload["posted"]), {"threads", "instagram", "facebook"})
                self.assertNotIn("posted_message_id", payload)
                self.assertFalse(self.messages(POSTED) or self.messages(MAIN))

    def test_caption_reply_retry_keeps_summary_and_archives_after_posting(self):
        def fail_caption_reply(method, path, body):
            if path == f"/channels/{MAIN}/messages":
                raise RuntimeError("caption reply failed")
            return {"id": "999"}

        self.api.side_effect = fail_caption_reply
        cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(self.state()[0], "published")
        self.assertEqual(self.state()[1]["posted_message_id"], "999")
        self.api.assert_any_call("PATCH", "/channels/201", {"archived": True})

        self.api.side_effect = None
        cli.cmd_publish(self.cfg, self.conn)
        cli.cmd_publish(self.cfg, self.conn)
        self.assertEqual(len(self.messages(POSTED)), 1)
        self.assertEqual(len(self.messages(MAIN)), 2)
        self.threads.assert_called_once()
        self.instagram.assert_called_once()
        self.facebook.assert_called_once()
