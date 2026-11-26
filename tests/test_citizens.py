"""citizens builder: line arithmetic, largest-remainder figures, null rows, specialists and its check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import citizens as C


def rec(scale=1.0, occupations=None, drop=()):
    """A synthetic ACS record with every citizen table line (male and female lines filled)."""
    r = {}
    occ = occupations or {4: 10, 7: 5, 11: 6, 16: 4, 20: 2, 19: 12, 27: 14, 30: 6, 31: 1, 34: 8}
    # C24010: male line n, female n + 36. 019 includes 020; 030 includes 031.
    for line, v in occ.items():
        r[f"C24010_{line:03d}"] = v * scale
        r[f"C24010_{line + 36:03d}"] = v * scale
    tot = sum(occ[l] for l in (4, 7, 11, 16, 19, 27, 30, 34)) * 2 * scale
    r["C24010_001"] = tot
    ages = 0
    for line in range(3, 26):
        r[f"B01001_{line:03d}"] = 3 * scale
        r[f"B01001_{line + 24:03d}"] = 2 * scale
        ages += 5 * scale
    r["B01001_001"] = ages
    r["B01002_001"] = 38.44
    commute = {3: 70, 4: 9, 8: 5, 9: 3, 10: 2, 11: 11}
    for line, v in commute.items():
        r[f"C08301_{line:03d}"] = v * scale
    r["C08301_001"] = 100 * scale
    for line in range(2, 19):
        r[f"C15003_{line:03d}"] = 10 * scale
    r["C15003_001"] = 170 * scale
    r.update({"B05002_001": 100 * scale, "B05002_002": 85 * scale, "B05002_003": 50 * scale,
              "B05002_004": 33 * scale, "B05002_013": 15 * scale})
    for t in drop:
        for k in [k for k in r if k.startswith(t)]:
            del r[k]
    return r


class LargestRemainderTest(unittest.TestCase):
    def test_sums_exactly(self):
        for vals in ({"a": 1, "b": 1, "c": 1}, {"a": 0.2, "b": 0.3, "c": 99.5}, {"a": 7, "b": 0, "c": 13}):
            for n in (1, 10, 20):
                self.assertEqual(sum(C.largest_remainder(vals, n).values()), n)

    def test_proportional_and_deterministic(self):
        self.assertEqual(C.largest_remainder({"a": 50, "b": 30, "c": 20}, 10), {"a": 5, "b": 3, "c": 2})
        # three equal remainders, one seat: the earliest key wins
        self.assertEqual(C.largest_remainder({"a": 1, "b": 1, "c": 1}, 1), {"a": 1, "b": 0, "c": 0})

    def test_zero_and_negative(self):
        self.assertEqual(C.largest_remainder({"a": 0, "b": 0}, 10), {"a": 0, "b": 0})
        self.assertEqual(C.largest_remainder({"a": -5, "b": 5}, 4), {"a": 0, "b": 4})


class RowTest(unittest.TestCase):
    def test_sexes_are_added_and_subtractions_applied(self):
        w = C.row(rec(), "workforce")
        self.assertEqual(w["counts"]["executive"], 20)
        self.assertEqual(w["counts"]["healer"], (4 + 2) * 2)
        self.assertEqual(w["counts"]["service"], (12 - 2) * 2)
        self.assertEqual(w["counts"]["builder"], (6 - 1) * 2)
        self.assertEqual(w["counts"]["farmer"], 2)
        self.assertEqual(sum(w["counts"].values()), w["total"])
        self.assertEqual(sum(w["figures"].values()), 20)

    def test_other_rows(self):
        r = rec()
        age = C.row(r, "age")
        self.assertEqual(age["counts"], {"child": 20, "young": 30, "midlife": 35, "elder": 30})
        self.assertEqual(age["median"], 38.4)
        self.assertEqual(sum(age["figures"].values()), 10)
        com = C.row(r, "commute")
        self.assertEqual(com["counts"]["home"], 11)
        self.assertEqual(sum(com["figures"].values()), 10)
        edu = C.row(r, "education")
        self.assertNotIn("figures", edu)
        self.assertAlmostEqual(edu["bachelors_plus"], 40 / 170, places=3)
        nat = C.row(r, "nativity")
        self.assertEqual(nat["counts"]["native_abroad"], 2)
        self.assertEqual(nat["foreign_born"], 0.15)
        self.assertEqual(sum(nat["counts"].values()), nat["total"])

    def test_missing_table_is_null_not_zero(self):
        r = rec(drop=["C24010"])
        self.assertIsNone(C.row(r, "workforce"))
        full = C.full_row(r, None, True)
        self.assertIsNone(full["workforce"])
        self.assertIsNotNone(full["age"])
        self.assertIsNone(C.full_row(None, None, True))
        self.assertIsNone(C.full_row({}, None, True))

    def test_specialists(self):
        nat = C.full_row(rec(), None, True)
        city = C.full_row(rec(occupations={4: 10, 7: 20, 11: 6, 16: 4, 20: 2, 19: 12, 27: 14, 30: 6, 31: 0, 34: 1}),
                          nat, False)
        sp = city["specialists"]
        self.assertEqual(sp[0]["key"], "engineer")
        self.assertGreaterEqual(sp[0]["ratio"], C.SPECIALIST_HIGH)
        self.assertTrue(any(s["key"] == "maker" and s["ratio"] <= C.SPECIALIST_LOW for s in sp))
        self.assertFalse(city["reliable"])
        highs = [s for s in sp if s["ratio"] >= 1]
        self.assertEqual(highs, sorted(highs, key=lambda s: -s["ratio"]))


class CheckTest(unittest.TestCase):
    def doc(self):
        nat = C.full_row(rec(), None, True)
        nat.pop("reliable")
        groups = {n: [{k: v for k, v in g.items() if k != "_spec"} for g in gs] for n, gs in C.GROUPS.items()}
        return {"version": 1, "vintage": "ACS 2025 1-year", "year": 2025, "sources": ["acs_1yr"],
                "figures": dict(C.FIGURES), "groups": groups, "national": nat,
                "cities": {"1": C.full_row(rec(2), nat, True), "2": None}}

    def run_check(self, doc, cities=None):
        with tempfile.TemporaryDirectory() as tmp:
            if doc is not None:
                (Path(tmp) / "citizens.json").write_text(json.dumps(doc))
            if cities is not None:
                (Path(tmp) / "cities.json").write_text(json.dumps(cities))
            return C.check(Path(tmp))

    def test_missing_file_is_a_note(self):
        self.assertEqual([lv for lv, _ in self.run_check(None)], ["NOTE"])

    def test_valid(self):
        lines = self.run_check(self.doc(), [{"id": "1"}, {"id": "2"}])
        self.assertFalse([m for lv, m in lines if lv == "FAIL"], lines)

    def test_violations(self):
        d = self.doc()
        d["cities"]["1"]["workforce"]["figures"]["engineer"] += 1
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(d)))
        d = self.doc()
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(d, [{"id": "1"}, {"id": "3"}])))
        d = self.doc()
        d["groups"]["age"].pop()
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(d)))


if __name__ == "__main__":
    unittest.main()
