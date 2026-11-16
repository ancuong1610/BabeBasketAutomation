import csv
import io
import json
import unittest
from pathlib import Path

from scraper import dm

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dm_crawl_page.json").read_text())


class ParseProductTest(unittest.TestCase):
    def setUp(self):
        self.row = dm.parse_product(FIXTURE["products"][0])

    def test_core_fields(self):
        self.assertEqual(self.row["store"], "dm")
        self.assertEqual(self.row["dan"], 1676340)
        self.assertEqual(self.row["brand"], "GARNIER FRUCTIS")
        self.assertEqual(self.row["name"], "Haarmaske Aloe Vera Hair Food 3in1, trockenes Haar, 400 ml")

    def test_price_is_a_number_in_euros(self):
        self.assertIsInstance(self.row["price_eur"], float)
        self.assertGreater(self.row["price_eur"], 0)

    def test_rating_and_review_count(self):
        self.assertIsInstance(self.row["rating"], float)
        self.assertIsInstance(self.row["review_count"], int)

    def test_urls_are_absolute(self):
        self.assertTrue(self.row["product_url"].startswith("https://www.dm.de/p/d/1676340/"))
        self.assertTrue(self.row["image_url"].startswith("https://products.dm-static.com/"))

    def test_image_url_is_large_version(self):
        self.assertIn("h_1200,w_1200", self.row["image_url"])


class ParsePriceTest(unittest.TestCase):
    def test_german_price_string(self):
        self.assertEqual(dm.parse_price("2,75 €"), 2.75)
        self.assertEqual(dm.parse_price("1.234,50 €"), 1234.5)

    def test_falls_back_to_price_string_without_tracking_data(self):
        product = json.loads(json.dumps(FIXTURE["products"][0]))
        del product["tileData"]["trackingData"]
        product["tileData"]["price"]["price"]["current"]["value"] = "4,45 €"
        self.assertEqual(dm.parse_product(product)["price_eur"], 4.45)

    def test_missing_rating_gives_empty_values(self):
        product = json.loads(json.dumps(FIXTURE["products"][0]))
        product["context"] = {}
        row = dm.parse_product(product)
        self.assertIsNone(row["rating"])
        self.assertEqual(row["review_count"], 0)


class CrawlTest(unittest.TestCase):
    def test_pages_until_total_pages_reached(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            page = len(calls) - 1
            return {"products": FIXTURE["products"], "currentPage": page, "totalPages": 2}

        rows = dm.crawl_category("Haarkur & Haarmaske", fetch=fake_fetch, delay=0)
        self.assertEqual(len(calls), 2)
        self.assertIn("categoryNames=Haarkur+%26+Haarmaske", calls[0])
        self.assertIn("currentPage=1", calls[1])
        # same products on both fake pages: duplicates are dropped by dan
        self.assertEqual(len(rows), len(FIXTURE["products"]))


class CsvTest(unittest.TestCase):
    def test_csv_has_header_and_rows(self):
        rows = [dm.parse_product(p) for p in FIXTURE["products"]]
        buf = io.StringIO()
        dm.write_csv(rows, buf)
        parsed = list(csv.DictReader(io.StringIO(buf.getvalue())))
        self.assertEqual(len(parsed), len(rows))
        self.assertEqual(list(parsed[0].keys()), dm.FIELDS)


if __name__ == "__main__":
    unittest.main()
