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
