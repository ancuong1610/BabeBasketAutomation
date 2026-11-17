import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from babebasket import postimage

LABELS = {
    "title": "Mixa Urea Cica Repair+",
    "hero": "10% Urea & Amino acid",
    "who_for": "Dành cho da khô, thô ráp",
    "points": ["Tái tạo da từ lần đầu dùng", "Giảm 90% vảy da chết"],
    "mode": "benefits",
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

    def test_render_draws_red_title_box(self):
        img = postimage.render(_scene(), LABELS)
        box = postimage.last_layout(img)["title"]
        x = (box[0] + box[2]) // 2
        y = box[1] + 6  # inside the box, above the text
        self.assertTrue(_close(img.getpixel((x, y)), postimage.RED), img.getpixel((x, y)))

    def test_long_vietnamese_line_wraps(self):
        labels = {**LABELS, "points": ["Giúp tóc chắc khỏe, giảm gãy rụng rõ rệt chỉ sau hai tuần sử dụng đều", "Tóc mượt"]}
        img = postimage.render(_scene(), labels)
        for box in postimage.last_layout(img)["points"]:
            self.assertLessEqual(box[2] - box[0], postimage.POINTS_MAX_W + 2 * postimage.PAD_X)
            self.assertLessEqual(box[2], 1080)

    def test_font_has_vietnamese(self):
        font = postimage.font(36)
        for ch in "ữậẫỡơư":
            self.assertIsNotNone(font.getmask(ch).getbbox(), ch)

    def test_old_labels_render(self):
        img = postimage.render(_scene(), {"title": "", "hero": "", "who_for": "Dành cho x", "points": ["a", "b"], "mode": "benefits"})
        self.assertEqual(img.size, (1080, 1350))
        self.assertNotIn("title", postimage.last_layout(img))

    def test_logo_is_drawn(self):
        img = postimage.render(_scene((10, 10, 10)), LABELS)
        x0, y0, x1, y1 = postimage.last_layout(img)["logo"]
        self.assertFalse(_close(img.getpixel(((x0 + x1) // 2, (y0 + y1) // 2)), (10, 10, 10)))

    def test_boxes_do_not_overlap(self):
        layout = postimage.last_layout(postimage.render(_scene(), LABELS))
        boxes = [layout["title"], layout["hero"], layout["who_for"], *layout["points"], layout["logo"]]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                self.assertFalse(overlap, (a, b))


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
