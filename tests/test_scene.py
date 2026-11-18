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

    def test_feedback_is_capped(self):
        parts = self._body(feedback="x" * 2000, previous=PNG)
        self.assertLess(len(parts[0]["text"]), 1500)


class CentredPromptTest(unittest.TestCase):
    def test_prompt_puts_product_in_the_centre_and_keeps_text_bands_calm(self):
        from babebasket import postimage, scene
        self.assertIn("CENTRE of the image horizontally", scene.PROMPT)
        self.assertNotIn("left of centre", scene.PROMPT)
        top, bottom = postimage.PRODUCT_ZONE
        self.assertIn(f"{round(top / postimage.H * 100)}% and {round(bottom / postimage.H * 100)}%", scene.PROMPT)
