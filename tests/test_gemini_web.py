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

    def test_k4_uses_real_evidence_as_second_input_or_third_for_edits(self):
        evidence = b"shop texture"
        for kwargs, expected, position in (({}, [PNG, evidence], "SECOND"),
                                           ({"feedback": "brighter", "previous": b"OLD"},
                                            [b"OLD", PNG, evidence], "THIRD")):
            with self.subTest(position=position), mock.patch.object(gemini_web, "generate_image") as gen:
                scene.generate(PNG, PRODUCT, CFG, style="k4", evidence=evidence, **kwargs)
                images, text, _ = gen.call_args.args
                self.assertEqual(images, expected)
                self.assertIn(f"The {position} attached image is a real photo", text)
                self.assertIn("copy its colour, shade, thickness, finish and texture exactly", text)

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
    def _prompt(self, style=None, product=PRODUCT, image_cfg=None, **kwargs):
        cfg = {"image": {"enabled": True, "backend": "gemini_web", **(image_cfg or {})}}
        with mock.patch.object(gemini_web, "generate_image", return_value=b"IMG") as gen:
            scene.generate(PNG, product, cfg, style=style, **kwargs)
        return gen.call_args.args[1]

    def test_k1_is_a_flat_lay_without_hands(self):
        text = self._prompt("k1")
        self.assertIn("flat lay", text)
        self.assertIn("No hands", text)
        self.assertIn("70-80% of the image height", text)
        self.assertNotIn("22%", text)
        self.assertIn("Mixa", text)

    def test_k0_stays_the_default_hand_in_aisle(self):
        text = self._prompt()
        self.assertIn("aisle", text)
        self.assertIn("top 20%", text)
        self.assertIn("NO yellow, orange or warm cast", text)
        self.assertIn("natural skin texture", text)

    def test_k3_is_a_close_up_hand_and_gradient_with_true_scale(self):
        text = self._prompt("k3")
        self.assertIn("hand holding", text)
        self.assertIn("gradient studio background", text)
        self.assertIn("the product holds 400 ml", text)

    def test_k4_without_evidence_shows_only_the_product(self):
        text = self._prompt("k4")
        self.assertIn("light marble or pastel surface", text)
        self.assertIn("Show only the product with no swatch", text)
        self.assertIn("only the CLOSED packaging", text)

    def test_other_styles_forbid_showing_contents_even_when_evidence_is_available(self):
        for style in ("k0", "k1", "k3"):
            for kwargs in ({}, {"feedback": "open the jar", "previous": b"OLD"}):
                with self.subTest(style=style, edit=bool(kwargs)):
                    text = self._prompt(style, evidence=b"shop texture", **kwargs)
                    self.assertIn("only the CLOSED packaging", text)
                    self.assertIn("Never show the product opened, applied, dripping or swatched", text)

    def test_all_styles_keep_packaging_rules_and_only_k0_reserves_text_space(self):
        for style in scene.STYLES:
            with self.subTest(style=style):
                text = self._prompt(style)
                self.assertEqual("top 20%" in text, style == "k0")
                self.assertIn("Exactly as many items", text)
                self.assertIn("same shape, colours, brand, logo and label text", text)
                self.assertIn("not cropped", text)
                self.assertIn("Do not add any text", text)
                self.assertIn("vertical 3:4 photo", text)

    def test_pair_notes_preserve_both_bottles_and_relative_sizes(self):
        pair = {**PRODUCT, "pair": {"name": "Conditioner"}}
        for style, note in {"k0": "two hands of the same woman", "k1": "lying next to each other",
                            "k3": "two hands of the same woman", "k4": "closed bottles side by side"}.items():
            with self.subTest(style=style):
                text = self._prompt(style, pair)
                self.assertIn(note, text)
                self.assertIn("Two bottles, not one", text)
                self.assertIn("true relative sizes", text)

    def test_edit_only_reserves_text_space_for_k0(self):
        for style in scene.STYLES:
            with self.subTest(style=style):
                text = self._prompt(style, feedback="brighter", previous=b"OLD")
                self.assertEqual("top 20%" in text, style == "k0")
                self.assertIn('"brighter"', text)

    def test_style_falls_back_through_config_and_unknowns_use_k0(self):
        self.assertIn("flat lay", self._prompt(image_cfg={"style": "k1"}))
        cfg = {"default_style": "k3", "style": "k1"}
        self.assertIn("gradient", self._prompt(image_cfg=cfg))
        self.assertIn("swatch", self._prompt("k4", image_cfg=cfg))
        self.assertIn("aisle", self._prompt("k2", image_cfg=cfg))
