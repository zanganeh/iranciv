"""districts builder: county sums with suppression, location quotients, the district rule, badges and check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import districts as D

OWNS = [1, 2, 3, 5]


def priv(emp, wages=None, chg=0):
    """A county entry with only private (own 5) jobs."""
    return [0, 0, 0, [emp, wages if wages is not None else emp * 50000, chg]]


def qcew():
    """Nation of 1,000,000 jobs; metro A = counties 01001 + 01003, B = 02001 (inland)."""
    us_frac = {"31_33": 0.08, "3361": 0.006, "3344": 0.003, "483": 0.0005, "4883": 0.0007}
    us = {c: [None, None, None, None] for c in D.BY_ID["industrial_zone"]["naics"]}
    codes = {c for d in D.CATALOG for c in d["naics"] + list(d["sub"])}
    us = {c: [None, None, None, [int(1_000_000 * us_frac.get(c, 0.01)), 1, 0]] for c in codes}
    counties = {c: {} for c in codes}
    # A: 100,000 jobs; manufacturing 20,000 (LQ 2.5), autos 6,000 (LQ 10)
    counties["31_33"]["01001"] = priv(15000)
    counties["31_33"]["01003"] = [0, 0, 0, None]  # suppressed
    counties["3361"]["01001"] = priv(6000, chg=1000)
    counties["483"]["01001"] = priv(3000)
    counties["4883"]["01001"] = priv(2000)
    # B: 50,000 jobs; plain-sized manufacturing (LQ 1.0) but semiconductors at LQ 8
    counties["31_33"]["02001"] = priv(4000)
    counties["3344"]["02001"] = priv(1200)
    counties["483"]["02001"] = priv(3000)
    return {"year": 2025, "vintage": "BLS QCEW 2025 annual averages", "owns": OWNS, "us": us,
            "us_total": [1_000_000, 60_000_000_000, 0],
            "totals": {"01001": [80000, 4e9, 0], "01003": [20000, 1e9, 0], "02001": [50000, 2e9, 0], "02003": None},
            "counties": counties, "codes": {c: c for c in codes}}


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.m = D.Metrics(qcew())

    def test_suppressed_counties_are_left_out_and_counted(self):
        v = self.m.industry(["31_33"], OWNS, ["01001", "01003"])
        self.assertEqual(v["emp"], 15000)
        self.assertEqual(v["suppressed"], 1)

    def test_all_suppressed_is_null_and_absent_is_zero(self):
        self.assertIsNone(self.m.industry(["31_33"], OWNS, ["01003"])["emp"])
        self.assertEqual(self.m.industry(["3254"], OWNS, ["01001"])["emp"], 0)

    def test_totals_skip_suppressed(self):
        self.assertEqual(self.m.total(["01001", "01003", "02003"]), {"emp": 100000, "wages": 5e9, "known": 2})
        self.assertIsNone(self.m.total(["02003"])["emp"])

    def test_lq(self):
        self.assertAlmostEqual(D.lq(20000, 100000, 80000, 1_000_000), 2.5)
        self.assertIsNone(D.lq(None, 100000, 80000, 1_000_000))
        self.assertIsNone(D.lq(5, 0, 80000, 1_000_000))
        self.assertEqual(D.excess(1000, 0.8), 0)
        self.assertAlmostEqual(D.excess(1000, 4), 750)


class RuleTest(unittest.TestCase):
    def setUp(self):
        self.m = D.Metrics(qcew())

    def test_broad_district_with_lower_bound(self):
        ds = D.evaluate(self.m, ["01001", "01003"], 100000)
        iz = next(d for d in ds if d["id"] == "industrial_zone")
        self.assertEqual(iz["via"], "broad")
        self.assertEqual(iz["sub"], "3361")
        self.assertTrue(iz["lower_bound"])
        self.assertEqual(iz["suppressed"], 1)

    def test_specialty_district(self):
        ds = D.evaluate(self.m, ["02001"], 50000, wet=False)
        iz = next(d for d in ds if d["id"] == "industrial_zone")
        self.assertEqual(iz["via"], "specialty")
        self.assertEqual(iz["sub"], "3344")
        self.assertLess(iz["lq"], D.MIN_LQ)

    def test_specialty_needs_min_jobs(self):
        q = qcew()
        q["counties"]["3344"]["02001"] = priv(400)
        ds = D.evaluate(D.Metrics(q), ["02001"], 50000, wet=False)
        self.assertNotIn("industrial_zone", [d["id"] for d in ds])

    def test_harbor_needs_water(self):
        self.assertIn("harbor", [d["id"] for d in D.evaluate(self.m, ["02001"], 50000, wet=True)])
        self.assertNotIn("harbor", [d["id"] for d in D.evaluate(self.m, ["02001"], 50000, wet=False)])

    def test_ranked_by_strength_and_capped(self):
        ds = D.evaluate(self.m, ["01001", "01003"], 100000)
        self.assertEqual([d["strength"] for d in ds], sorted((d["strength"] for d in ds), reverse=True))
        self.assertLessEqual(len(ds), D.MAX_PER_CITY)

    def test_badges(self):
        ds = D.evaluate(self.m, ["01001", "01003"], 100000)
        b = D.badge_for(ds[0])
        self.assertEqual(b["district"], ds[0]["id"])
        self.assertIsNone(D.badge_for(None))
        best = {"id": "industrial_zone", "lq": 1.4, "sub": "3361", "sub_lq": 7.0}
        self.assertEqual(D.badge_for(best)["label"], "Motor city")
        self.assertEqual(D.badge_for(dict(best, sub_lq=2.0))["label"], "Factory town")
        gov = {"id": "government_plaza", "lq": 3.0, "sub": "926", "sub_lq": 5.0}
        self.assertEqual(D.badge_for(gov, capital=True)["label"], "Capital city")

    def test_one_vocabulary_of_nicknames(self):
        for d in D.CATALOG:
            label = d["badge"]["label"]
            self.assertNotRegex(label, r"(al|ic|ary)$", d["id"])  # no bare adjectives ('Industrial', 'Militaristic')
            self.assertIn(" ", label)

    def test_extreme_district_names_the_badge(self):
        # Huntsville-like: a big factory base ranks first, but defense civilians are 13× and 4.7% of jobs
        ds = [{"id": "industrial_zone", "lq": 1.6, "share": 0.13, "strength": 13245},
              {"id": "encampment", "lq": 13.0, "share": 0.047, "strength": 11693},
              {"id": "aerodrome", "lq": 3.0, "share": 0.028, "strength": 5730}]
        self.assertEqual(D.badge_district(ds)["id"], "encampment")
        ds[1]["share"] = 0.02  # extreme but small: the strongest district stays
        self.assertEqual(D.badge_district(ds)["id"], "industrial_zone")
        self.assertIsNone(D.badge_district([]))

    def test_college_town_when_campus_withheld(self):
        univ = [("Penn State", 50000)]
        b = D.college_badge(univ, 160000, campus_withheld=True)
        self.assertEqual((b["label"], b["district"], b["basis"], b["withheld"]), ("College town", "campus", "ipeds", True))
        self.assertIsNone(D.college_badge(univ, 160000, campus_withheld=False))
        self.assertIsNone(D.college_badge(univ, 1_000_000, campus_withheld=True))  # 5% < 15%
        self.assertIsNone(D.college_badge([], 160000, campus_withheld=True))


class CheckTest(unittest.TestCase):
    def doc(self):
        m = D.Metrics(qcew())
        ds = D.evaluate(m, ["01001", "01003"], 100000)
        return {"version": 1, "vintage": "v", "year": 2025, "sources": ["bls_qcew"], "rule": {"text": D.RULE_TEXT},
                "catalog": D.CATALOG, "industries": {"3361": {"title": "x", "us_emp": 6000}},
                "national": {"emp": 1_000_000, "avg_pay": 60000},
                "cities": {"A": {"emp": 100000, "avg_pay": 50000, "counties": [2, 2],
                                 "top": [{"naics": "3361", "emp": 6000, "lq": 10.0}],
                                 "districts": ds, "badge": D.badge_for(ds[0])},
                           "B": None},
                "counties": {}}

    def run_check(self, doc, cities=None):
        with tempfile.TemporaryDirectory() as tmp:
            if doc is not None:
                (Path(tmp) / "districts.json").write_text(json.dumps(doc))
            if cities is not None:
                (Path(tmp) / "cities.json").write_text(json.dumps(cities))
            return D.check(Path(tmp))

    def test_missing_file_is_a_note(self):
        self.assertEqual([lv for lv, _ in self.run_check(None)], ["NOTE"])

    def test_valid(self):
        lines = self.run_check(self.doc(), [{"id": "A"}, {"id": "B"}])
        self.assertFalse([m for lv, m in lines if lv == "FAIL"], lines)

    def test_violations(self):
        cases = {
            "badge": lambda d: d["cities"]["A"].update(badge=None),
            "badge district": lambda d: d["cities"]["A"]["badge"].update(district="spaceport"),
            "ipeds badge": lambda d: d["cities"]["A"].update(badge={"district": "campus", "basis": "ipeds"}),
            "lower_bound": lambda d: d["cities"]["A"]["districts"][0].update(lower_bound=False, suppressed=3),
            "unknown": lambda d: d["cities"]["A"]["districts"].append({"id": "spaceport", "emp": 1}),
            "catalog": lambda d: d["catalog"].pop(),
            "top": lambda d: d["cities"]["A"]["top"].append({"naics": "9999"}),
        }
        for what, change in cases.items():
            d = self.doc()
            change(d)
            with self.subTest(what=what):
                self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(d)), what)
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(self.doc(), [{"id": "A"}, {"id": "C"}])))


class CapitalTest(unittest.TestCase):
    def test_state_capital_metros_only(self):
        h = D.holds_state_capital
        self.assertTrue(h({"full": "Sacramento-Roseville-Folsom, CA Metro Area", "states": ["CA"]}))
        self.assertTrue(h({"full": "Minneapolis-St. Paul-Bloomington, MN-WI Metro Area", "states": ["MN", "WI"]}))
        self.assertFalse(h({"full": "Fresno, CA Metro Area", "states": ["CA"]}))
        self.assertFalse(h({"full": "Columbia, MO Metro Area", "states": ["MO"]}))  # Columbia is SC's capital
        self.assertFalse(h({"full": "Parkersburg-Vienna, WV Metro Area", "states": ["WV"]}))

    def test_seat_of_government_label(self):
        best = {"id": "government_plaza", "sub": "921", "sub_lq": 9.0, "lq": 2.0, "lower_bound": False,
                "sub_lower_bound": True}
        self.assertEqual(D.badge_for(best, state_capital=True)["label"], "Seat of government")
        b = D.badge_for(best, state_capital=False)
        self.assertNotEqual(b["label"], "Seat of government")
        self.assertIn("≥9.0×", b["text"])


if __name__ == "__main__":
    unittest.main()
