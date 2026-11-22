import unittest

from babebasket import catalog, db


def cls(name, cats="", brand=""):
    return catalog.classify({"name": name, "categories": cats, "brand": brand})[0]


class ClassifyTest(unittest.TestCase):
    def test_groups(self):
        cases = {
            ("Shampoo Repair", "Shampoo"): "hair",
            ("Haarkur Glow", "Haarkur & Haarmaske"): "hair",
            ("Bodylotion Urea", "Bodylotion & Hautcreme"): "body",
            ("Deo Roll-on", "Deo"): "body",
            ("Fußcreme", "Fußpflege"): "body",
            ("Badesalz Lavendel", "Badezusatz & Badesalz"): "body",
            ("Intimwaschlotion", "Intimpflege & -gesundheit"): "body",
            ("Duschgel Damen Frische", "Duschgel"): "body",          # "Damen" is not "men"
            ("Duschgel Sport", "Duschen für Männer"): "extra",
            ("Sonnenspray LSF 50 Körper", "Sonnenpflege"): "extra",
            ("Tagescreme Hyaluron", "Gesichtspflege"): "extra",
            ("Haut Haare Nägel Kapseln", "Wohlbefinden & Stärkung"): "extra",
            ("Sättigungskapseln", "Abnehmprodukte"): "extra",
            ("Geschenkset Duschpflege", "Geschenksets; Duschgel"): "extra",
            ("Shampoo & Conditioner After Sun 2in1", "Conditioner; Shampoo"): "hair",
            ("Styling Creme Curlz", "Locken Produkte; Haargel"): "hair",
            ("Hitzeschutzspray Keratin", "Hitzeschutzspray & Co."): "hair",
            ("Lippenmaske Smushy SOS", "Lippenpflege"): "extra",
            ("Anti Pickel Patches", "Anti Pickel & Mitesser"): "extra",
            ("Augen Roll-On Aqua", "Gesichtspflege"): "extra",
            ("Bartbalsam Barber Club", "Bartpflege"): "extra",
            ("Kapseln für den Nährstoffbedarf der Haarwurzel", "Unterstützung fürs Haar"): "extra",
        }
        for (name, cats), group in cases.items():
            self.assertEqual(cls(name, cats), group, name)

    def test_never_sold(self):
        for name, cats in [("Hornhautraspel", "Fußpflege"), ("Badeschwamm", "Körperreinigung"),
                           ("Massageöl", "Körperöl"), ("Vitamin D3 Tabletten", "Vitamine"),
                           ("Zahnpasta", "Zahnpasta"), ("Haarglätteisen", "Haarstyling"),
                           ("Eau de Parfum", "Parfum"), ("Spielzeug", "Spielwaren"),
                           ("Selbstbräuner Schaum", "Selbstbräuner & Bräunungsbeschleuniger"),
                           ("Stylinggel Kleber 4 Brows & Edges", "Haargel"),
                           ("Einlegesohlen", "Einlegesohlen")]:
            self.assertIsNone(cls(name, cats), name)
        for name, cats in [("Kinder Duschgel Erdbeere", "Kinderduschgel"), ("Baby Shampoo sensitiv", "Babypflege")]:
            self.assertIsNone(cls(name, cats), name)

    def test_sprays_are_left_to_the_warning_check(self):
        self.assertEqual(cls("Trockenshampoo", "Haarstyling"), "hair")
        self.assertEqual(cls("Haarspray Extra Strong", "Haarstyling"), "hair")

    def test_only_jean_and_len_hair_perfume(self):
        self.assertEqual(cls("Haarparfum Rose & Cedarwood", "Haarstyling", "Jean&Len"), "hair")
        self.assertIsNone(cls("Haarparfum", "Haarstyling", "Other Brand"))
        self.assertIsNone(cls("Eau de Parfum", "Parfum", "Jean&Len"))


class PairTest(unittest.TestCase):
    def p(self, pid, name, brand="L'ORÉAL PARiS ELVITAL", rating=4.5, reviews=100):
        return {"store": "dm", "product_id": pid, "brand": brand, "name": name, "price_eur": 3.45,
                "rating": rating, "review_count": reviews}

    def test_kinds(self):
        self.assertEqual(catalog.hair_kind(self.p("1", "Shampoo Glycolic Gloss, 200 ml")), "shampoo")
        self.assertEqual(catalog.hair_kind(self.p("1", "Spülung Repair, 200 ml")), "conditioner")
        self.assertIsNone(catalog.hair_kind(self.p("1", "Sprüh-Conditioner Express Repair, 200 ml")))
        self.assertEqual(catalog.hair_kind(self.p("1", "Shampoo & Conditioner 2in1, 250 ml")), "2in1")
        self.assertIsNone(catalog.hair_kind(self.p("1", "Haarkur Glow, 200 ml")))

    def test_exact_twin_wins_even_with_lower_rating(self):
        shampoo = self.p("s", "Shampoo Hydra Hyaluronic, feuchtigkeitsspendend, 1.000 ml")
        rows = [shampoo,
                self.p("c1", "Conditioner Hydra Hyaluronic, feuchtigkeitsversiegelnd, 200 ml", rating=3.4),
                self.p("c2", "Conditioner Hydra Hyaluronic Pure, 200 ml", brand="L'ORÉAL PARiS Elvital", rating=4.4)]
        self.assertEqual(catalog.find_partner(shampoo, rows)["product_id"], "c1")

    def test_line_contained_and_spacing(self):
        gliss = self.p("s", "Shampoo Full Hair Magic, 250 ml", "Schwarzkopf GLISS")
        rows = [gliss, self.p("c", "Conditioner Full Hair Magic, 200 ml", "Schwarzkopf GLISS")]
        self.assertEqual(catalog.find_partner(gliss, rows)["product_id"], "c")
        hydra = self.p("s", "Shampoo Hydra Hyaluronic, 250 ml")
        self.assertEqual(catalog.find_partner(hydra, [hydra, self.p("c", "Conditioner Hydra Hyaluronic Pure, 200 ml")])
                         ["product_id"], "c")
        honey = self.p("s", "Shampoo Honigschätze, 1.000 ml", "Wahre Schätze")
        rows = [honey, self.p("c", "Conditioner Honig Schätze, 250 ml", "Wahre Schätze")]
        self.assertEqual(catalog.find_partner(honey, rows)["product_id"], "c")

    def test_draft_45_express_conditioner_is_not_a_twin(self):
        gliss = self.p("s", "Shampoo Full Hair Magic, 250 ml", "Schwarzkopf GLISS")
        for name in ["Conditioner Express Repair Full Hair Magic, 200 ml", "Spülung Express Full Hair Magic, 200 ml",
                     "Conditioner Leave-In Full Hair Magic, 200 ml", "Spülung Kur Full Hair Magic, 200 ml",
                     "Conditioner Full Hair Magic Maske, 200 ml", "Express-Spülung Full Hair Magic, 200 ml"]:
            self.assertIsNone(catalog.hair_kind(self.p("c", name)), name)
            self.assertIsNone(catalog.find_partner(gliss, [gliss, self.p("c", name, "Schwarzkopf GLISS")]), name)
        # a real rinse-out twin of a different sub-line is not matched through the treatment words either
        self.assertFalse(catalog.same_line(self.p("a", "Shampoo Full Hair Magic"),
                                           self.p("b", "Conditioner Full Hair Magic Serum")))

    def test_no_pair_across_brands_lines_or_poor_ratings(self):
        shampoo = self.p("s", "Shampoo Colour Protect, 300 ml", "PANTENE PRO-V")
        rows = [shampoo,
                self.p("a", "Conditioner Colour Protect, 200 ml", "Other Brand"),
                self.p("b", "Conditioner Grow Abundant, 250 ml", "PANTENE PRO-V"),
                self.p("c", "Conditioner Colour Protect, 200 ml", "PANTENE PRO-V", rating=2.5)]
        self.assertIsNone(catalog.find_partner(shampoo, rows))


DRY_SHAMPOO = {"descriptionGroups": [
    {"header": "Produktbeschreibung", "contentBlock": [{"texts": ["Trockenshampoo"]}]},
    {"header": "Warnhinweise", "contentBlock": [{"texts": [
        "Extrem entzündbares Aerosol. Behälter steht unter Druck: Kann bei Erwärmung bersten."]}]},
]}
AEROSOL_NO_WARNING = {"descriptionGroups": [
    {"header": "Warnhinweise", "contentBlock": [{"texts": ["Nicht auf gereizte Haut sprühen."]}]},
    {"header": "Inhaltsstoffe", "contentBlock": [{"texts": ["INGREDIENTS: Butane, Isobutane, Propane, Parfum"]}]},
]}
PUMP_SPRAY = {"descriptionGroups": [
    {"header": "Warnhinweise", "contentBlock": [{"texts": ["Kontakt mit den Augen vermeiden."]}]},
]}


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.calls = []

    def checker(self, pages):
        def fetch(pid):
            self.calls.append(pid)
            return pages[pid]
        return catalog.make_safety_checker(self.conn, fetch_detail=fetch, delay=0)

    def test_dm_aerosol_is_unsafe_pump_spray_is_fine(self):
        check = self.checker({"1": DRY_SHAMPOO, "2": PUMP_SPRAY})
        self.assertEqual(check({"store": "dm", "product_id": "1"})[0], False)
        self.assertEqual(check({"store": "dm", "product_id": "2"}), (True, ""))

    def test_gas_spray_without_fire_warning_is_blocked(self):
        ok, reason = self.checker({"3": AEROSOL_NO_WARNING})({"store": "dm", "product_id": "3", "name": "Deospray"})
        self.assertFalse(ok)
        self.assertIn("propellant", reason)

    def test_pump_spray_and_propanediol_ship(self):
        pump = {"descriptionGroups": [{"header": "Inhaltsstoffe", "contentBlock": [
            {"texts": ["Aqua, Propanediol, Butylene Glycol, Glycerin, Parfum"]}]}]}
        self.assertEqual(self.checker({"6": pump})({"store": "dm", "product_id": "6", "name": "Leave-In Spray"}),
                         (True, ""))

    def test_fire_warning_on_a_non_spray_ships(self):
        gel = {"descriptionGroups": [{"header": "Warnhinweise", "contentBlock": [
            {"texts": ["Flüssigkeit und Dampf leicht entzündbar."]}]}]}
        self.assertTrue(self.checker({"4": gel})({"store": "dm", "product_id": "4", "name": "Handgel"})[0])

    def test_fire_warning_on_a_spray_named_can_is_blocked(self):
        liquid_warning = {"descriptionGroups": [{"header": "Warnhinweise", "contentBlock": [
            {"texts": ["Entzündbar. Von Zündquellen fernhalten."]}]}]}
        ok, reason = self.checker({"5": liquid_warning})({"store": "dm", "product_id": "5", "name": "Haarspray Strong"})
        self.assertFalse(ok)
        self.assertIn("fire warning", reason)

    def test_result_is_cached(self):
        check = self.checker({"1": DRY_SHAMPOO})
        check({"store": "dm", "product_id": "1"})
        check({"store": "dm", "product_id": "1"})
        self.assertEqual(self.calls, ["1"])

    def test_rossmann_goes_by_name(self):
        check = self.checker({})
        self.assertFalse(check({"store": "rossmann", "product_id": "9", "name": "Deospray Fresh"})[0])
        self.assertTrue(check({"store": "rossmann", "product_id": "8", "name": "Schaumbad Lavendel"})[0])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
