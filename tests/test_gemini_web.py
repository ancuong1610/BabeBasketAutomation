import tempfile
import unittest
from pathlib import Path
from unittest import mock

from babebasket import gemini_web, scene

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
PRODUCT = {"brand": "Mixa", "name": "Urea Cica Repair+, 400 ml"}
CFG = {"image": {"enabled": True, "backend": "gemini_web", "gemini_web": {"min_gap": 0, "max_gap": 0}}}


class GeminiWebTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(gemini_web, n, tmp / f) for n, f in
                        (("HOME", "."), ("COUNTER", "count.json"), ("STOPPED", "stopped"))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_scene_uses_gemini_web_with_3_4_prompt(self):
        with mock.patch.object(gemini_web, "_run", return_value=b"IMG") as run:
            out = scene.generate(PNG, PRODUCT, CFG, post=lambda *a, **k: self.fail("no OpenRouter call"))
        self.assertEqual(out, b"IMG")
        images, prompt, ratio, _ = run.call_args.args
        self.assertEqual(images, [PNG])
        self.assertIn("Mixa", prompt)
        self.assertIn("3:4", prompt)
        self.assertNotIn("4:5", prompt)
        self.assertEqual(ratio, "3:4")

    def test_edit_sends_previous_then_packshot(self):
        with mock.patch.object(gemini_web, "_run", return_value=b"IMG") as run:
            scene.generate(PNG, PRODUCT, CFG, feedback="brighter", previous=b"OLD")
        self.assertEqual(run.call_args.args[0], [b"OLD", PNG])

    def test_daily_cap(self):
        cfg = {"image": {**CFG["image"], "gemini_web": {"min_gap": 0, "max_gap": 0, "daily_cap": 2}}}
        with mock.patch.object(gemini_web, "_run", return_value=b"IMG") as run:
            results = [scene.generate(PNG, PRODUCT, cfg) for _ in range(3)]
        self.assertEqual(results, [b"IMG", b"IMG", None])
        self.assertEqual(run.call_count, 2)

    def test_stop_marks_and_blocks_next_calls(self):
        with mock.patch.object(gemini_web, "_run", side_effect=gemini_web.GeminiStop("captcha")) as run, \
                mock.patch.object(gemini_web, "_tell_owner") as tell:
            self.assertIsNone(scene.generate(PNG, PRODUCT, CFG))
            self.assertIsNone(scene.generate(PNG, PRODUCT, CFG))
        self.assertEqual(run.call_count, 1)
        tell.assert_called_once()
        self.assertIn("captcha", gemini_web.stopped())

    def test_failure_returns_none(self):
        with mock.patch.object(gemini_web, "_run", side_effect=TimeoutError("selector")):
            self.assertIsNone(scene.generate(PNG, PRODUCT, CFG))


class SshGuardTest(unittest.TestCase):
    def test_never_launches_chrome_over_ssh(self):
        with mock.patch.dict("os.environ", {"SSH_CONNECTION": "1.2.3.4 1 5.6.7.8 22"}), \
                mock.patch("playwright.sync_api.sync_playwright") as pw:
            with self.assertRaisesRegex(RuntimeError, "SSH"):
                gemini_web._run([PNG], "p", "3:4", 10)
        pw.assert_not_called()


class StyleTest(unittest.TestCase):
    def _prompt(self, style, product=PRODUCT):
        cfg = {"image": {"enabled": True, "backend": "gemini_web", "style": style}}
        with mock.patch.object(gemini_web, "generate_image", return_value=b"IMG") as gen:
            scene.generate(PNG, product, cfg)
        return gen.call_args.args[1]

    def test_k1_is_a_flat_lay_without_hands(self):
        text = self._prompt("k1")
        self.assertIn("flat lay", text)
        self.assertIn("No hands", text)
        self.assertNotIn("drugstore aisle", text)
        self.assertIn("Mixa", text)

    def test_k0_stays_the_default_hand_in_aisle(self):
        self.assertIn("aisle", self._prompt("k0"))

    def test_k1_pair_asks_for_both_bottles(self):
        text = self._prompt("k1", {**PRODUCT, "pair": {"name": "Conditioner"}})
        self.assertIn("Two bottles, not one", text)
