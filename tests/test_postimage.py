import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from babebasket import postimage

LABELS = {
    "title": "Kem dưỡng ẩm Mixa Urea Cica",
    "subtitle": "Dành cho da khô, thô ráp",
    "points": ["Tái tạo da từ lần đầu dùng", "Giảm 90% vảy da chết"],
    "kind": "body",
}


def _scene(color=(200, 200, 200), size=(900, 1200)) -> Image.Image:
    return Image.new("RGB", size, color)


def _packshot() -> bytes:
    img = Image.new("RGBA", (600, 800), (255, 255, 255, 255))
    img.paste((40, 90, 200, 255), (150, 100, 450, 700))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _close(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class RenderTest(unittest.TestCase):
    def test_render_size(self):
        img = postimage.render(_scene(), LABELS)
        self.assertEqual((img.size, img.mode), ((1080, 1350), "RGB"))

    def test_title_is_white_text_with_dark_outline_and_no_box(self):
        img = postimage.render(_scene((128, 128, 128)), LABELS)
        x0, y0, x1, y1 = postimage.last_layout(img)["title"]
        px = [img.getpixel((x, y)) for x in range(x0, x1, 2) for y in range(y0, y1, 2)]
        self.assertTrue(any(min(p) > 240 for p in px), "white letters")
        self.assertTrue(any(max(p) < 50 for p in px), "dark outline")
        self.assertTrue(_close(img.getpixel((x0 + 1, y0 + 1)), (128, 128, 128), tol=20), "no box behind text")
        self.assertFalse(any(_close(p, (224, 38, 43), tol=30) for p in px), "no red")

    def test_title_is_centred(self):
        x0, _, x1, _ = postimage.last_layout(postimage.render(_scene(), LABELS))["title"]
        self.assertLessEqual(abs((x0 + x1) / 2 - 540), 4)

    def test_long_title_wraps_into_balanced_lines(self):
        f = postimage.font(postimage.TITLE_SIZE, title=True)
        lines = postimage.wrap_balanced("Sữa dưỡng thể Mixa Ceramide Protect", f, postimage.TITLE_MAX_W)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l.split()) >= 2 for l in lines), lines)
        self.assertEqual(postimage.wrap_balanced("Kem NIVEA Soft", f, postimage.TITLE_MAX_W), ["Kem NIVEA Soft"])

    def test_text_stays_out_of_the_product_zone(self):
        layout = postimage.last_layout(postimage.render(_scene(), LABELS))
        top, bottom = postimage.PRODUCT_ZONE
        self.assertLessEqual(layout["subtitle"][3], top)
        self.assertGreaterEqual(min(b[1] for b in layout["points"]), bottom)

    def test_long_vietnamese_line_wraps(self):
        labels = {**LABELS, "points": ["Giúp tóc chắc khỏe, giảm gãy rụng rõ rệt chỉ sau hai tuần sử dụng đều", "Tóc mượt"]}
        img = postimage.render(_scene(), labels)
        for box in postimage.last_layout(img)["points"]:
            self.assertLessEqual(box[2] - box[0], postimage.POINTS_MAX_W + 2 * postimage.TEXT_STROKE)
            self.assertLessEqual(box[2], 1080)

    def test_font_has_vietnamese(self):
        font = postimage.font(36)
        for ch in "ữậẫỡơư":
            self.assertIsNotNone(font.getmask(ch).getbbox(), ch)

    def test_old_labels_render(self):
        img = postimage.render(_scene(), {"title": "", "subtitle": "Dành cho x", "points": ["a", "b"], "kind": "other"})
        self.assertEqual(img.size, (1080, 1350))
        self.assertNotIn("title", postimage.last_layout(img))

    def test_logo_is_drawn(self):
        img = postimage.render(_scene((10, 10, 10)), LABELS)
        x0, y0, x1, y1 = postimage.last_layout(img)["logo"]
        self.assertFalse(_close(img.getpixel(((x0 + x1) // 2, (y0 + y1) // 2)), (10, 10, 10)))

    def test_clean_render_has_only_the_same_round_logo(self):
        base = _scene((10, 10, 10))
        clean = postimage.render_clean(base)
        logo = postimage.last_layout(postimage.render(base, LABELS))["logo"]
        self.assertEqual(clean.size, (1080, 1350))
        self.assertEqual(postimage.last_layout(clean), {"logo": logo})
        self.assertEqual(clean.getpixel((540, 60)), (10, 10, 10))
        x0, y0, x1, y1 = logo
        self.assertFalse(_close(clean.getpixel(((x0 + x1) // 2, (y0 + y1) // 2)), (10, 10, 10)))

    def test_boxes_do_not_overlap(self):
        layout = postimage.last_layout(postimage.render(_scene(), LABELS))
        boxes = [layout["title"], layout["subtitle"], *layout["points"], layout["logo"]]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                self.assertFalse(overlap, (a, b))


class ProductAreaTest(unittest.TestCase):
    def test_fallback_product_is_centred(self):
        img = postimage.fallback_scene(_packshot())
        top, bottom = postimage.PRODUCT_ZONE
        mid_y = (top + bottom) // 2
        self.assertFalse(_close(img.getpixel((540, mid_y)), img.getpixel((60, mid_y))), "product in the middle")


class FallbackTest(unittest.TestCase):
    def test_fallback_scene_size(self):
        img = postimage.fallback_scene(_packshot())
        self.assertEqual(img.size, (1080, 1350))

    def test_save_writes_jpeg(self):
        with tempfile.TemporaryDirectory() as d:
            path = postimage.save(postimage.render(_scene(), LABELS), Path(d) / "x" / "post.jpg")
            with Image.open(path) as im:
                self.assertEqual(im.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
