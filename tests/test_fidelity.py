"""The AI image must look like the real packaging: facts are read first, the result is checked after."""

import io
import json
import unittest
from unittest import mock

from PIL import Image

from babebasket import cli, db, discord, postimage, scene

CFG = {"image": {"enabled": True, "max_per_day": 6, "max_per_post": 3},
       "llm": {"model": "m"}, "discord": {"channel_id": "CH", "owner_user_ids": []}}


def png(color="red", size=(20, 40)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


class DescribeTest(unittest.TestCase):
    def test_facts_become_prompt_lines(self):
        post = mock.Mock(return_value=reply({"products": [
            {"container": "flip-cap bottle", "opacity": "opaque, iridescent", "cap_or_pump": "pink flip cap"}]}))
        facts = scene.describe_packaging(png(), CFG, post=post, api_key="k")
        self.assertIn("opacity: opaque, iridescent", facts)
        self.assertIn("cap or pump: pink flip cap", facts)
        body = post.call_args.kwargs["body"]
        self.assertEqual(body["model"], "m")                  # the cheap text model reads the photo
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")

    def test_failure_gives_no_facts(self):
        self.assertEqual(scene.describe_packaging(png(), CFG, post=mock.Mock(side_effect=RuntimeError("x")), api_key="k"), "")

    def test_facts_go_into_new_and_edit_prompts(self):
        for kw in ({}, {"feedback": "to hơn", "previous": png("blue")}):
            post = mock.Mock(return_value={})
            scene.generate(png(), {"name": "Shampoo, 200 ml"}, CFG, post=post, api_key="k",
                           facts="- product 1: opacity: opaque", **kw)
            text = post.call_args.kwargs["body"]["messages"][0]["content"][0]["text"]
            self.assertIn("opacity: opaque", text, kw)


class CheckTest(unittest.TestCase):
    def check(self, answer=None, error=None, facts=""):
        post = mock.Mock(return_value=reply(answer), side_effect=error)
        return scene.check_fidelity(png(), png("blue"), CFG, post=post, api_key="k", facts=facts), post

    def test_mismatch_with_problems(self):
        result, _ = self.check({"match": False, "problems": ["thân chai trong suốt thay vì đục"]})
        self.assertEqual(result, {"match": False, "problems": ["thân chai trong suốt thay vì đục"], "checked": True})

    def test_mismatch_without_reasons_gets_a_generic_one(self):
        self.assertEqual(self.check({"match": False})[0]["problems"], ["ảnh AI khác bao bì thật"])

    def test_check_failure_does_not_block(self):
        self.assertEqual(self.check(error=RuntimeError("down"))[0], {"match": True, "problems": [], "checked": False})

    def test_facts_are_given_to_the_checker(self):
        _, post = self.check({"match": True}, facts="- product 1: opacity: opaque")
        text = post.call_args.kwargs["body"]["messages"][0]["content"][0]["text"]
        self.assertIn("opacity: opaque", text)
        self.assertIn('"match"', text)   # the JSON example survives filling in the facts


class BuildImageTest(unittest.TestCase):
    def run_build(self, payload, verdict):
        describe = mock.Mock(return_value="- product 1: opacity: opaque")
        gen = mock.Mock(return_value=png("green", (40, 50)))
        check = mock.Mock(return_value=verdict)
        with mock.patch.object(postimage, "save", side_effect=lambda img, out: out):
            cli.build_image(payload, "/tmp/x.jpg", CFG, fetch_bytes=lambda u: png(), gen=gen,
                            describe=describe, check=check)
        return describe, gen, check

    def test_facts_read_once_passed_to_model_and_result_checked(self):
        payload = {"product": {"product_id": "1", "image_url": "u", "name": "Shampoo"}, "draft": {}}
        describe, gen, check = self.run_build(payload, {"match": True, "problems": []})
        self.assertEqual(gen.call_args.kwargs["facts"], "- product 1: opacity: opaque")
        self.assertEqual(check.call_args.kwargs["facts"], "- product 1: opacity: opaque")
        self.assertEqual(payload["fidelity"]["match"], True)
        describe2, _, _ = self.run_build(payload, {"match": True, "problems": []})
        describe2.assert_not_called()                     # cached in the payload for edits

    def test_plain_background_is_not_checked(self):
        payload = {"product": {"product_id": "1", "image_url": "u"}, "draft": {}}
        with mock.patch.object(postimage, "save", side_effect=lambda img, out: out):
            cli.build_image(payload, "/tmp/x.jpg", CFG, fetch_bytes=lambda u: png(), gen=mock.Mock(),
                            ai=False, describe=mock.Mock(), check=mock.Mock())
        self.assertNotIn("fidelity", payload)


class CardWarningTest(unittest.TestCase):
    def test_mismatch_is_shown_on_the_image_card(self):
        conn = db.connect(":memory:")
        payload = {"product": {"store": "dm", "product_id": "1", "image_url": "u", "brand": "B", "name": "N"},
                   "draft": {"captions": [{"text": "x"}]}}
        draft_id = db.add_draft(conn, "dm", "1", payload)
        db.set_status(conn, draft_id, "approved", discord_message_id="CAP")
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()

        def fake_build(p, out, cfg, **kw):
            p.update(image_path=str(out), image_source="gemini",
                     fidelity={"match": False, "problems": ["thân chai trong suốt thay vì đục"]})
            return str(out)

        cards = []
        with mock.patch.object(cli, "build_image", side_effect=fake_build), \
                mock.patch.object(discord, "unreact"), \
                mock.patch.object(discord, "post_image_card", side_effect=lambda ch, card, path, n: cards.append(card) or "M"):
            cli.send_image_card(CFG, conn, row, payload, ai=True)
        self.assertIn("⚠️ Có thể khác bao bì thật: thân chai trong suốt thay vì đục", cards[0]["content"])


if __name__ == "__main__":
    unittest.main()
