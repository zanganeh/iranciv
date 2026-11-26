"""County lenses: Faith (USRC 2020), Amenities (CDC PLACES), Disasters (OpenFEMA, NOAA), Veterans (ACS)."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import disasters, lenses
from pipeline.sources import noaa_billion, openfema


class FaithTest(unittest.TestCase):
    def rec(self, **kw):
        base = {"pop": 1000, "adherents": 600, "evangelical": 100, "mainline": 50, "black_protestant": 0,
                "catholic": 150, "orthodox": 0, "other": 300, "lds": 280}
        return {**base, **kw}

    def test_rate_is_computed_from_counts(self):
        f = lenses.faith_county(self.rec())
        self.assertAlmostEqual(f["faith"], 60.0)

    def test_lds_is_split_out_of_other(self):
        f = lenses.faith_county(self.rec())
        self.assertEqual(f["trad"]["other"], 20)
        self.assertEqual(f["trad"]["lds"], 280)
        cats = [c["id"] for c in lenses.FAITH_CATEGORIES]
        self.assertEqual(cats[lenses.dominant_tradition(f["trad"])], "lds")

    def test_blank_total_is_null_not_zero(self):
        self.assertIsNone(lenses.faith_county(self.rec(adherents=None)))
        self.assertIsNone(lenses.faith_county(None))

    def test_no_adherents_has_no_dominant_tradition(self):
        zero = {c["id"]: 0 for c in lenses.FAITH_CATEGORIES}
        self.assertIsNone(lenses.dominant_tradition(zero))

    def test_categories_match_the_contract(self):
        self.assertEqual([c["id"] for c in lenses.FAITH_CATEGORIES],
                         ["evangelical", "mainline", "black_protestant", "catholic", "orthodox", "lds", "other"])


class AmenitiesTest(unittest.TestCase):
    def test_index_centres_on_100_and_never_imputes(self):
        places = {
            "a": {"mhlth": 10, "depression": 20, "ghlth": 10, "lpa": 20},
            "b": {"mhlth": 20, "depression": 30, "ghlth": 20, "lpa": 30},
            "c": {"mhlth": None, "depression": 30, "ghlth": 20, "lpa": 30},
        }
        idx, stats = lenses.amenities_by_county(places, ["a", "b", "c", "d"])
        self.assertAlmostEqual(idx["a"], 110.0)  # one SD better on all four measures
        self.assertAlmostEqual(idx["b"], 90.0)
        self.assertIsNone(idx["c"])  # a measure missing (e.g. Kentucky in PLACES 2025)
        self.assertIsNone(idx["d"])  # no PLACES row
        self.assertAlmostEqual(stats["mhlth"][0], 15.0)


class DeclarationsTest(unittest.TestCase):
    fema = {
        "disasters": {
            "1": {"state": "AL", "type": "Hurricane", "date": "2005-08-29", "counties": ["01001", "01003"], "statewide": False},
            "2": {"state": "AL", "type": "Tornado", "date": "2011-04-28", "counties": ["01001"], "statewide": True},
            "3": {"state": "CT", "type": "Snowstorm", "date": "2013-02-08", "counties": ["09001"], "statewide": False},
            "4": {"state": "AK", "type": "Earthquake", "date": "2018-11-30", "counties": ["02020"], "statewide": False},
        },
        "ct_crosswalk": {"09001": ["09120", "09190"]},
    }

    def test_counts_types_and_last_date(self):
        per = disasters.county_declarations(self.fema, ["01001", "01003", "09120", "09190", "09110"])
        self.assertEqual(per["01001"]["total"], 2)
        self.assertEqual(per["01001"]["hurricane"], 1)
        self.assertEqual(per["01001"]["severe_storm"], 1)  # Tornado → severe storms & tornadoes
        self.assertEqual(per["01001"]["last_date"], "2011-04-28")
        self.assertEqual(per["01003"]["total"], 1)
        for g in ("09120", "09190"):  # old Fairfield County → the planning regions overlapping it
            self.assertEqual(per[g]["winter"], 1)
        self.assertEqual(per["09110"]["total"], 0)
        self.assertIsNone(per["09110"]["last_date"])
        for rec in per.values():
            self.assertEqual(sum(rec[t] for t in disasters.TYPE_IDS), rec["total"])

    def test_unknown_incident_type_is_other(self):
        self.assertEqual(disasters.incident_type("Volcanic Eruption"), "other")
        self.assertEqual(disasters.incident_type("Severe Ice Storm"), "winter")

    def test_page_url_filters_dr_without_biological(self):
        url = openfema.page_url(20000)
        self.assertIn("declarationType%20eq%20'DR'", url)
        self.assertIn("incidentType%20ne%20'Biological'", url)
        self.assertIn("$skip=20000", url)
        self.assertIn("$orderby=id", url)


class NoaaTest(unittest.TestCase):
    def test_title_lines_are_skipped(self):
        text = "1980-2024 Billion-Dollar Disasters Cost (in $Millions)\nstate,\"drought\"\nTX,12.5\n"
        self.assertEqual(noaa_billion._table(text, "state"), [{"state": "TX", "drought": "12.5"}])


class ChecksTest(unittest.TestCase):
    def test_missing_outputs_are_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual({lv for lv, _ in disasters.check(tmp)}, {"NOTE"})
            self.assertEqual({lv for lv, _ in lenses.check(tmp)}, {"NOTE"})
            (Path(tmp) / "lenses.json").write_text(json.dumps({"vintage": "x", "groups": [], "lenses": []}))
            self.assertEqual({lv for lv, _ in lenses.check(tmp)}, {"NOTE"})

    def doc(self):
        cols = ["geoid", "total", *disasters.TYPE_IDS, "last_date"]
        return {
            "version": 1, "vintage": {}, "sources": ["fema_declarations", "noaa_billions"],
            "fema": {"window": ["2000-01-01", "2026-01-01"], "types": disasters.FEMA_TYPES,
                     "counties": {"columns": cols, "rows": [["01001", 2, 1, 0, 0, 1, 0, 0, "2011-04-28"]]},
                     "states": {"AL": {"total": 2, "statewide": 1, "by_type": {"hurricane": 1, "severe_storm": 1}}},
                     "recent": [{"date": "2026-01-01"}, {"date": "2025-06-01"}]},
            "noaa": {"years": [1980, 2024], "final": True, "states": {"AL": {}},
                     "national": {"events": 3, "cost_usd": 1e10, "by_year": [{"year": 1980, "events": 3, "cost_usd": 1e10}]}},
        }

    def run_check(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "disasters.json").write_text(json.dumps(doc))
            (Path(tmp) / "world.json").write_text(json.dumps({"counties": [{"geoid": "01001", "name": "Autauga County"}]}))
            return disasters.check(tmp)

    def test_good_file_passes(self):
        lines = self.run_check(self.doc())
        self.assertFalse([m for lv, m in lines if lv == "FAIL"], lines)

    def test_types_not_summing_to_total_fail(self):
        doc = self.doc()
        doc["fema"]["counties"]["rows"][0][1] = 5
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(doc)))

    def test_wrong_county_order_fails(self):
        doc = self.doc()
        doc["fema"]["counties"]["rows"][0][0] = "01003"
        self.assertTrue(any(lv == "FAIL" for lv, _ in self.run_check(doc)))


class ContractTest(unittest.TestCase):
    """The faith, wellbeing, disaster and veteran lenses in the built file (skipped when web/data has not been built with them)."""

    def test_built_lenses(self):
        path = Path(__file__).resolve().parents[1] / "web" / "data" / "lenses.json"
        if not path.exists():
            self.skipTest("web/data/lenses.json not built")
        by_id = {l["id"]: l for l in json.loads(path.read_text())["lenses"]}
        if "faith" not in by_id:
            self.skipTest("faith/wellbeing/disaster/veteran lenses not built yet")
        self.assertEqual(by_id["faith"]["domain"], [0, 100])
        self.assertEqual(by_id["faith_tradition"]["format"], "category")
        for lid in ("faith", "faith_tradition", "amenities", "disasters"):
            self.assertTrue(by_id[lid].get("note"), lid)
        self.assertTrue(all(v is None or 0 <= v <= 1 for v in by_id["veterans"]["values"]))
        self.assertTrue(all(v is None or isinstance(v, int) for v in by_id["disasters"]["values"]))


if __name__ == "__main__":
    unittest.main()
