"""The Discord photo picker through the scheduled image check, using local photos and a fake API."""

import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from PIL import Image

from babebasket import cli, db, discord, http, postimage, scene, threads
from tests.test_gift_photos import PHOTO_CFG
from tests.test_owner_controls import jpg


CFG = {**PHOTO_CFG, "discord": {"channel_id": "CH", "owner_user_ids": ["42"]},
       "image": {}, "threads": {"enabled": True}}
PACKSHOT = "https://shop/images/f_auto,q_auto,c_fit,h_800,w_800/pack"
SWATCH = "https://shop/swatch.jpg"
LABEL = "https://shop/label.jpg"
DROP = "https://cdn/owner.jpg?signature=abc"


class ContactSheetTest(unittest.TestCase):
    def test_tiles_are_numbered_letterboxed_and_show_the_selection(self):
        photo = jpg((200, 400), "pink")
        candidates = [{"type": "lip_swatch", "source": "shop"}] * 9
        data = postimage.render_picker_sheet(candidates, [2, 9], [photo] * 9)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(image.width, 1600)
            tile, gap = postimage.PICKER_TILE, postimage.PICKER_GAP
            badges = set()
            for index in range(9):
                row, col = divmod(index, 3)
                x, y = gap + col * (tile + gap), gap + row * (tile + 58 + gap)
                with self.subTest(tile=index + 1):
                    border = image.getpixel((x + 5, y + 180))
                    if index + 1 in (2, 9):
                        self.assertGreater(border[1], border[0] + 65)
                        mark = image.getpixel((x + tile - 66, y + 75))
                        self.assertGreater(min(mark), 190)
                    else:
                        self.assertGreater(min(border), 230)
                    self.assertGreater(min(image.getpixel((x + 25, y + 220))), 230)
                    r, g, _ = image.getpixel((x + tile // 2, y + 210))
                    self.assertGreater(r, g + 35)
                    badge = image.crop((x + 37, y + 38, x + 90, y + 90)).convert("L")
                    self.assertGreater(badge.getextrema()[1], 220)
                    badges.add(badge.point(lambda gray: 255 if gray > 140 else 0).tobytes())
            self.assertEqual(len(badges), 9)


class PickerFlowTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hero = self.data / "post.jpg"
        self.hero.write_bytes(jpg(color="black"))
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.photos = {PACKSHOT: jpg(color="red"), SWATCH: jpg(color="pink"),
                       LABEL: jpg(color="blue"), DROP: jpg(color="green")}
        self.reactions, self.reactors = {}, {}
        self.messages, self.uploads, self.replies, self.events = {}, [], [], []
        self.incoming, self.fail_once = [], None
        self.next_id = 100
        self.enterContext(mock.patch.object(cli, "DATA", self.data))
        self.enterContext(mock.patch.object(cli, "fetch_bytes", side_effect=self.photos.__getitem__))
        self.enterContext(mock.patch.object(discord, "secret", return_value="test"))
        self.enterContext(mock.patch.object(discord, "_call", side_effect=self.api))
        self.enterContext(mock.patch.object(discord, "request_multipart", side_effect=self.upload))
        self.enterContext(mock.patch.object(discord.time, "sleep"))
        self.build = self.enterContext(mock.patch.object(cli, "build_image", side_effect=self.build_hero))
        self.classify = self.enterContext(mock.patch.object(scene, "classify_gallery", return_value=[
            {"url": scene.small_jpg(PACKSHOT, 1200), "type": "product_hero"},
            {"url": SWATCH, "type": "lip_swatch", "this_shade": True},
            {"url": LABEL, "type": "label"},
        ]))

    def new_message_id(self):
        self.next_id += 1
        return str(self.next_id)

    def api(self, method, path, body=None):
        if method == "POST":
            message_id = self.new_message_id()
            self.replies.append((message_id, body["message_reference"]["message_id"], body["content"]))
            return {"id": message_id}
        if method == "GET" and "/messages?" in path:
            after = int(path.split("after=")[1].split("&")[0])
            self.events.append(("replies", after))
            return [m for m in self.incoming if int(m["id"]) > after]
        route = path.split("?")[0].split("/")
        message_id = route[4]
        if method == "PUT":
            self.reactions.setdefault(message_id, []).append(unquote(route[6]))
        if method in ("PUT", "DELETE"):
            return None
        if len(route) > 5:
            return self.reactors.get(message_id, {}).get(unquote(route[6]), [])
        self.events.append(("get", message_id))
        return {**self.messages[message_id], "reactions": [
            {"emoji": {"name": emoji}, "count": len(users) + 1, "me": True}
            for emoji, users in self.reactors.get(message_id, {}).items()
        ]}

    def upload(self, url, *, fields, files, method="POST", **kwargs):
        message_id = self.new_message_id() if method == "POST" else url.rsplit("/", 1)[1]
        if self.fail_once == message_id:
            self.fail_once = None
            raise RuntimeError("Discord unavailable")
        card = json.loads(fields["payload_json"])
        self.uploads.append((method, message_id, card, files))
        self.events.append((method, message_id))
        attachment_urls = {filename: f"https://cdn/{message_id}/{len(self.uploads)}/{filename}"
                           for _, filename, _, _ in files}
        embeds = [{**embed, "image": {"url": attachment_urls[embed["image"]["url"].split("://")[1]]}}
                  for embed in card["embeds"]]
        self.messages[message_id] = {**card, "embeds": embeds, "attachments": []}
        return {"id": message_id}

    def build_hero(self, payload, out, cfg, **kwargs):
        payload.update(image_path=str(self.hero), image_source="gemini")
        return str(self.hero)

    def post(self, store="dm"):
        product = {"store": store, "name": "Lip gloss", "group": "makeup", "image_url": PACKSHOT}
        if store == "dm":
            product["gallery"] = [PACKSHOT, SWATCH, LABEL]
        payload = {"product": product, "draft": {"captions": [{"text": "Son xinh 💗"}]}}
        self.draft_id = db.add_draft(self.conn, store, "1", payload)
        db.set_status(self.conn, self.draft_id, "approved", discord_message_id="90", chosen_caption=1)
        cli.cmd_images(CFG, self.conn)
        _, payload = self.state()
        self.image, self.picker = payload["image_message_id"], payload["picker"]["message_id"]

    def state(self):
        row = self.conn.execute("SELECT status, payload FROM drafts WHERE id = ?", (self.draft_id,)).fetchone()
        return row["status"], json.loads(row["payload"])

    def tap(self, message_id, *emojis):
        self.reactors[message_id] = {emoji: [{"id": "42"}] for emoji in emojis}

    def check(self):
        cli.cmd_check_images(CFG, self.conn)

    def test_new_card_gets_one_picker_in_shop_order_and_the_initial_plan(self):
        self.post()
        _, payload = self.state()
        self.assertEqual(payload["picker"]["candidates"], [
            {"url": PACKSHOT, "type": "packshot", "source": "packshot"},
            {"url": SWATCH, "type": "lip_swatch", "source": "shop"},
            {"url": LABEL, "type": "label", "source": "shop"},
        ])
        self.assertEqual(payload["picker"]["selected"], [2])
        self.assertEqual(payload["supporting"], [SWATCH])
        self.assertEqual(self.reactions[self.picker], ["1️⃣", "2️⃣", "3️⃣", "0️⃣", "📷"])
        method, mid, card, files = self.uploads[-1]
        self.assertEqual((method, mid), ("POST", self.picker))
        self.assertEqual(card["message_reference"]["message_id"], self.image)
        self.assertEqual((card["nonce"], card["enforce_nonce"]), (f"pick-{self.image}", True))
        self.assertIn("Đang chọn: 2 (bot tự chọn)", card["content"])
        self.assertEqual(files[0][1], "bang-anh.jpg")
        self.assertIn("đổi ở bảng ảnh bên dưới", self.messages[self.image]["content"])
        self.check()
        self.assertEqual(len(self.uploads), 2)
        self.classify.assert_called_once()

    def test_rossmann_packshot_has_a_picker_without_classification(self):
        self.post(store="rossmann")
        self.assertEqual(self.reactions[self.picker], ["1️⃣", "0️⃣", "📷"])
        self.assertEqual(self.state()[1]["picker"]["candidates"],
                         [{"url": PACKSHOT, "type": "packshot", "source": "packshot"}])
        self.classify.assert_not_called()

    def test_nine_tiles_seed_only_eleven_reactions(self):
        gallery = []
        for index in range(10):
            url = f"https://shop/gallery-{index}.jpg"
            gallery.append({"url": url, "type": "other"})
            self.photos[url] = self.photos[LABEL]
        self.classify.return_value = gallery
        self.post()
        self.assertEqual([p["url"] for p in self.state()[1]["picker"]["candidates"]],
                         [PACKSHOT, *(p["url"] for p in gallery[:8])])
        self.assertEqual(self.reactions[self.picker], [*discord.PICKER_NUMBERS, "0️⃣", "📷"])

    def test_numbers_un_tap_zero_and_default_ignore_bots_and_other_users(self):
        self.post()
        for emojis, selected in [(("3️⃣", "1️⃣"), [1, 3]), (("3️⃣",), [3]),
                                 (("0️⃣", "3️⃣"), []), ((), [2])]:
            with self.subTest(emojis=emojis):
                self.tap(self.picker, *emojis)
                self.reactors[self.picker].update({"2️⃣": [{"id": "90", "bot": True}, {"id": "91"}]})
                self.events.clear()
                self.check()
                state, payload = self.state()
                self.assertEqual((state, payload["picker"]["selected"]), ("image_pending", selected))
                self.assertEqual(payload["supporting_attached"], len(selected))
                self.assertEqual(self.events.count(("get", self.picker)), 1)
        self.assertEqual(db.image_calls_for(self.conn, self.draft_id), 1)
        self.build.assert_called_once()

    def test_patch_keeps_card_reactions_and_same_minute_approval_publishes_new_uploads(self):
        self.post()
        self.tap(self.picker, "3️⃣", "1️⃣")
        self.tap(self.image, "✅")
        self.events.clear()
        self.check()
        method, mid, card, files = self.uploads[-2]
        self.assertEqual((method, mid), ("PATCH", self.image))
        self.assertEqual(files[0][2], self.hero.read_bytes())
        self.assertEqual([a["filename"] for a in card["attachments"]], ["post.jpg", "support-1.jpg", "support-2.jpg"])
        self.assertEqual([f[1] for f in files], ["post.jpg", "support-1.jpg", "support-2.jpg"])
        self.assertEqual(self.uploads[-1][:2], ("PATCH", self.picker))
        self.assertEqual(self.events[:4], [("get", self.picker), ("PATCH", self.image), ("PATCH", self.picker),
                                          ("get", self.image)])
        status, payload = self.state()
        self.assertEqual((status, payload["image_message_id"], payload["supporting"]), ("ready", self.image, [PACKSHOT, LABEL]))
        urls = [embed["image"]["url"] for embed in self.messages[self.image]["embeds"]]
        with mock.patch.object(threads, "maybe_refresh", return_value=False), \
                mock.patch.object(threads, "publish", return_value={"id": "T", "permalink": "https://t/p"}) as publish:
            cli.cmd_publish(CFG, self.conn)
        publish.assert_called_once_with("Son xinh 💗", urls[0], extra_images=urls[1:])
        self.assertEqual(self.state()[0], "published")
        self.assertEqual(db.image_calls_for(self.conn, self.draft_id), 1)
        self.build.assert_called_once()

    def test_owner_drop_saves_a_jpeg_updates_both_messages_and_releases_approval(self):
        self.post()
        self.tap(self.picker, "📷", "3️⃣")
        self.tap(self.image, "✅")
        self.check()
        state, payload = self.state()
        self.assertEqual(state, "image_pending")
        prompt, target, text = self.replies[-1]
        self.assertEqual(target, self.picker)
        self.assertIn("Kéo thả ảnh của bạn", text)
        self.assertEqual(payload["picker"]["photo_prompt"], prompt)
        self.incoming = [{"id": self.new_message_id(), "author": {"id": "42"}, "content": "",
                          "attachments": [{"url": DROP, "filename": "owner.jpg"}]}]
        self.events.clear()
        self.check()
        state, payload = self.state()
        owner = payload["picker"]["candidates"][-1]
        photo, = (self.data / "sources").glob("*.jpg")
        self.assertEqual(owner, {"url": photo.resolve().as_uri(), "type": "owner", "source": "owner"})
        self.assertEqual(photo.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertEqual(payload["picker"]["selected"], [3, 4])
        self.assertEqual(payload["supporting"], [LABEL, owner["url"]])
        self.assertEqual(state, "ready")
        self.assertEqual(self.reactions[self.picker][-1], "4️⃣")
        self.assertEqual(self.events[0][0], "replies")
        self.assertIn("Đang chọn: 3, 4", self.messages[self.picker]["content"])
        self.assertEqual(db.image_calls_for(self.conn, self.draft_id), 1)
        self.build.assert_called_once()

    def test_owner_can_reply_to_prompt_and_change_the_selection_later(self):
        self.post()
        self.tap(self.picker, "📷")
        self.check()
        prompt = self.replies[-1][0]
        attachment = {"url": DROP, "filename": "owner.jpg"}
        self.incoming = [{"id": self.new_message_id(), "author": {"id": "42"}, "content": "",
                          "message_reference": {"message_id": prompt}, "attachments": [attachment]}]
        self.check()
        self.check()
        self.assertEqual(self.state()[1]["picker"]["selected"], [2, 4])
        self.tap(self.picker, "1️⃣")
        self.check()
        self.assertEqual(self.state()[1]["picker"]["selected"], [1])
        self.tap(self.picker)
        self.check()
        self.assertEqual(self.state()[1]["picker"]["selected"], [2])
        self.assertEqual(len(self.replies), 1)

    def test_retried_owner_photo_is_never_treated_as_an_ai_edit(self):
        self.post()
        self.tap(self.picker, "📷")
        self.check()
        self.incoming = [{"id": self.new_message_id(), "author": {"id": "42"}, "content": "Ảnh của mình",
                          "attachments": [{"url": DROP, "filename": "owner.jpg"}]}]
        self.tap(self.image, "✅")
        self.fail_once = self.image
        self.check()
        self.assertEqual(self.state()[0], "image_pending")
        self.check()
        self.assertEqual(self.state()[0], "ready")
        self.assertEqual(self.state()[1]["picker"]["selected"], [2, 4])
        self.build.assert_called_once()

    def test_wait_expires_after_ten_minutes(self):
        self.post()
        self.tap(self.picker, "📷")
        self.tap(self.image, "✅")
        when = dt.datetime.fromisoformat(db.now()) - dt.timedelta(minutes=10)
        with mock.patch.object(db, "now", return_value=when.isoformat()):
            self.check()
        self.assertEqual(self.state()[0], "image_pending")
        self.check()
        self.assertEqual(self.state()[0], "ready")

    def test_typed_fallback_still_edits_the_picker_card_in_place(self):
        self.post()
        self.incoming = [{"id": self.new_message_id(), "author": {"id": "42"}, "content": "thêm ảnh 3",
                          "message_reference": {"message_id": self.image}}]
        self.check()
        self.check()
        state, payload = self.state()
        self.assertEqual((state, payload["image_message_id"], payload["supporting"]),
                         ("image_pending", self.image, [SWATCH, LABEL]))
        self.assertEqual([(method, mid) for method, mid, _, _ in self.uploads[-2:]],
                         [("PATCH", self.image), ("PATCH", self.picker)])
        self.build.assert_called_once()

    def test_failed_patch_holds_approval_and_retries_without_a_new_card(self):
        self.post()
        self.tap(self.picker, "1️⃣")
        self.tap(self.image, "✅")
        self.fail_once = self.image
        self.check()
        state, payload = self.state()
        self.assertEqual((state, payload["supporting"]), ("image_pending", [SWATCH]))
        self.fail_once = self.picker
        self.check()
        state, payload = self.state()
        self.assertEqual((state, payload["supporting"]), ("image_pending", [PACKSHOT]))
        self.assertTrue(payload["picker"]["dirty"])
        self.check()
        self.assertEqual(self.state()[0], "ready")
        self.assertEqual([(method, mid) for method, mid, _, _ in self.uploads],
                         [("POST", self.image), ("POST", self.picker), ("PATCH", self.image), ("PATCH", self.picker)])


class MultipartTest(unittest.TestCase):
    def test_patch_uses_the_requested_http_method(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"101"}'
        with mock.patch.object(http.urllib.request, "urlopen", return_value=response) as send:
            http.request_multipart("https://discord.test/messages/101", method="PATCH", fields={}, files=[])
        self.assertEqual(send.call_args.args[0].get_method(), "PATCH")
