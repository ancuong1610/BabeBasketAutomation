import base64
import unittest

from babebasket import scene

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
CFG = {"image": {"model": "google/gemini-3.1-flash-image", "enabled": True}}
PRODUCT = {"brand": "Mixa", "name": "Urea Cica Repair+, 400 ml"}


def _reply_with_image(data: bytes = PNG) -> dict:
    url = "data:image/png;base64," + base64.b64encode(data).decode()
    return {"choices": [{"message": {"content": "", "images": [{"type": "image_url", "image_url": {"url": url}}]}}]}


class SceneTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def _post(self, reply=None, error=None):
        def post(url, **kw):
            self.calls.append((url, kw))
            if error:
                raise error
            return reply
        return post

    def test_returns_image_bytes(self):
        out = scene.generate(PNG, PRODUCT, CFG, post=self._post(_reply_with_image()), api_key="k")
        self.assertEqual(out, PNG)

    def test_no_image_returns_none(self):
        reply = {"choices": [{"message": {"content": "I cannot do that."}}]}
        self.assertIsNone(scene.generate(PNG, PRODUCT, CFG, post=self._post(reply), api_key="k"))

    def test_error_returns_none(self):
        post = self._post(error=RuntimeError("HTTP 500"))
        self.assertIsNone(scene.generate(PNG, PRODUCT, CFG, post=post, api_key="k"))

    def test_disabled_returns_none_without_call(self):
        cfg = {"image": {**CFG["image"], "enabled": False}}
        self.assertIsNone(scene.generate(PNG, PRODUCT, cfg, post=self._post(_reply_with_image()), api_key="k"))
        self.assertEqual(self.calls, [])

    def test_request_shape(self):
        scene.generate(PNG, PRODUCT, CFG, post=self._post(_reply_with_image()), api_key="k")
        (url, kw), = self.calls
        body = kw["body"]
        self.assertEqual(body["model"], "google/gemini-3.1-flash-image")
        self.assertEqual(body["modalities"], ["image", "text"])
        parts = body["messages"][0]["content"]
        image_parts = [p for p in parts if p["type"] == "image_url"]
        self.assertTrue(image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        text = " ".join(p["text"] for p in parts if p["type"] == "text")
        self.assertIn("Mixa", text)
        self.assertEqual(kw["headers"]["Authorization"], "Bearer k")


if __name__ == "__main__":
    unittest.main()


class FeedbackTest(unittest.TestCase):
    def _body(self, **kw):
        calls = []

        def post(url, **k):
            calls.append(k)
            return _reply_with_image()
        scene.generate(PNG, PRODUCT, CFG, post=post, api_key="k", **kw)
        return calls[0]["body"]["messages"][0]["content"]

    def test_edit_sends_previous_scene_then_packshot(self):
        parts = self._body(feedback="  product   bigger ", previous=b"\x89PNG\r\n\x1a\nprev")
        self.assertIn('"product bigger"', parts[0]["text"])
        self.assertIn("SECOND attached image", parts[0]["text"])
        self.assertEqual([p["type"] for p in parts], ["text", "image_url", "image_url"])
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,iVBORw0KGgpwcmV2"))

    def test_feedback_without_previous_extends_the_normal_prompt(self):
        parts = self._body(feedback="hand on the left")
        self.assertIn("Mixa", parts[0]["text"])
        self.assertIn('"hand on the left"', parts[0]["text"])
        self.assertEqual(len(parts), 2)

    def test_openrouter_k4_includes_the_evidence_in_order_for_new_images_and_edits(self):
        evidence = b"shop contents"
        for kwargs, images in (({}, [PNG, evidence]),
                               ({"feedback": "more light", "previous": b"old scene"}, [b"old scene", PNG, evidence])):
            with self.subTest(kwargs=kwargs):
                parts = self._body(style="k4", evidence=evidence, **kwargs)
                actual = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in parts[1:]]
                self.assertEqual(actual, images)

    def test_feedback_is_capped(self):
        parts = self._body(feedback="x" * 2000, previous=PNG)
        feedback = parts[0]["text"].split('"', 2)[1]
        self.assertEqual(len(feedback), scene.MAX_FEEDBACK_CHARS)


class ScaleTest(unittest.TestCase):
    def test_volume_parsing(self):
        self.assertEqual(scene._volume_ml("Shampoo, 1.000 ml"), 1000)
        self.assertEqual(scene._volume_ml("Duschgel, 1 l"), 1000)
        self.assertEqual(scene._volume_ml("Öl, 2,5 ml"), 2.5)
        self.assertEqual(scene._volume_ml("Balm, 50 ml"), 50)
        self.assertIsNone(scene._volume_ml("Geschenkset"))

    def test_one_litre_bottle_is_big_and_heavy(self):
        hint = scene.size_hint("Shampoo Hydra Hyaluronic, 1.000 ml")
        self.assertIn("1.000 ml", hint)
        self.assertIn("heavy", hint)
        self.assertIn("do not shrink", hint)

    def test_small_jar_keeps_its_unit(self):
        self.assertIn("25 g, small", scene.size_hint("Tuchmaske, 25 g"))

    def test_prompt_has_asian_hand_and_scale(self):
        captured = {}

        def post(url, **kw):
            captured.update(kw)
            return {}
        scene.generate(PNG, {"brand": "B", "name": "Shampoo, 1.000 ml"}, CFG, post=post, api_key="k")
        text = captured["body"]["messages"][0]["content"][0]["text"]
        self.assertIn("East/Southeast Asian woman", text)
        self.assertIn("16 cm", text)
        self.assertIn("1.000 ml", text)


class CentredPromptTest(unittest.TestCase):
    def test_prompt_puts_product_in_the_centre_and_keeps_text_bands_calm(self):
        from babebasket import postimage, scene
        self.assertIn("CENTRE of the image horizontally", scene.PROMPT)
        self.assertNotIn("left of centre", scene.PROMPT)
        top, bottom = postimage.PRODUCT_ZONE
        self.assertIn(f"{round(top / postimage.H * 100)}% and {round(bottom / postimage.H * 100)}%", scene.PROMPT)
