"""Military layer: MIRTA/DMDC helpers, the Encampment rule and military.json's check()."""

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from pipeline.build import military
from pipeline.sources import dmdc, mirta

from test_check import dataset


def square(lon0, lat0, size):
    return [[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size], [lon0, lat0]]


class GeometryHelpersTest(unittest.TestCase):
    def test_polygon_area_is_equal_area_and_subtracts_holes(self):
        one = mirta.polygon_area_km2({"type": "Polygon", "coordinates": [square(-100, 40, 1)]})
        self.assertAlmostEqual(one, 111.2 * 111.2 * 0.7604, delta=one * 0.01)  # ~1° x 1° at 40.5°N
        holed = mirta.polygon_area_km2({"type": "Polygon", "coordinates": [square(-100, 40, 1), square(-99.75, 40.25, 0.5)]})
        self.assertAlmostEqual(holed, one * 0.75, delta=one * 0.01)
        multi = mirta.polygon_area_km2({"type": "MultiPolygon", "coordinates": [[square(-100, 40, 1)], [square(-90, 40, 1)]]})
        self.assertAlmostEqual(multi, 2 * one, delta=one * 0.02)
        self.assertIsNone(mirta.polygon_area_km2(None))

    def test_simplify_drops_collinear_points_and_tiny_rings(self):
        ring = [[0, 0], [0.5, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        rings = mirta.simplify_rings({"type": "Polygon", "coordinates": [ring, square(5, 5, 0.0001)]}, tol=0.01)
        self.assertEqual(len(rings), 1)
        self.assertNotIn([0.5, 0], rings[0])

    def test_component_codes_from_both_layers(self):
        self.assertEqual(mirta.component_id("usa"), "army")
        self.assertEqual(mirta.component_id("USA"), "army")
        self.assertEqual(mirta.component_id("armyNationalGuard"), "army_guard")
        self.assertEqual(mirta.component_id("Army National Guard"), "army_guard")
        self.assertEqual(mirta.component_id("WHS"), "other")
        self.assertEqual({c["id"] for c in military.COMPONENTS}, set(mirta.COMPONENTS.values()) | {"other"})

    def test_dmdc_quarters_newest_first_and_only_past(self):
        q = dmdc._quarters(date(2026, 11, 25))
        self.assertEqual(q[:3], ["2609", "2606", "2603"])
        self.assertEqual(dmdc._quarters(date(2026, 9, 10))[0], "2606")

    def test_county_name_keys(self):
        k = military._county_key
        self.assertEqual(k("Fairfax County"), k("Fairfax"))
        self.assertNotEqual(k("Fairfax city"), k("Fairfax"))
        self.assertEqual(k("Norfolk City"), k("Norfolk city"))
        self.assertEqual(k("St. Louis County"), k("St Louis"))


def inst(id, county, component="army", tier="base", status="act", area=50.0, cell=0):
    return {"id": id, "name": id, "component": component, "tier": tier, "status": status, "county": county,
            "area_km2": area, "cell": cell}


class EncampmentRuleTest(unittest.TestCase):
    def setUp(self):
        self.loc = SimpleNamespace(cities={
            "A": {"counties": ["01001"], "footprint": [0]},
            "B": {"counties": ["01003"], "footprint": [3]},
            "C": {"counties": ["01005"], "footprint": [4]},
            "D": {"counties": ["01007"], "footprint": [9]},
        })

    def acs(self, **af):
        # county → (armed forces, population 16+)
        return {f"0500000US{g}": {"B23025_006": a, "B23025_001": p} for g, (a, p) in af.items()}

    def test_tiers_thresholds_and_exclusions(self):
        sites = [inst("fort", "01001"), inst("armory", "01003", component="army_guard"),
                 inst("office", "01005", tier="site"), inst("ship", "01007", component="navy")]
        acs = self.acs(**{"01001": (500, 10000), "01003": (900, 10000), "01005": (900, 10000), "01007": (150, 10000)})
        out = military.cities_block(self.loc, sites, [], acs)
        self.assertEqual(out["A"]["tier"], "fortress")  # 5%
        self.assertNotIn("B", out)  # Guard only
        self.assertNotIn("C", out)  # a small 'site' does not count
        self.assertEqual(out["D"]["tier"], "garrison")  # 1.5%
        self.assertTrue(out["D"]["naval"])
        self.assertFalse(out["A"]["naval"])
        self.assertEqual(out["A"]["share"], 0.05)

    def test_count_threshold_and_range_footprint(self):
        acs = self.acs(**{"01001": (2500, 1_000_000), "01003": (1500, 1_000_000)})
        rng = [{"cells": [3], "installation": "range"}]
        sites = [inst("fort", "01001"), inst("range", "09999", tier="major", area=900)]
        out = military.cities_block(self.loc, sites, rng, acs)
        self.assertNotIn("A", out)  # 2,500 but only 0.25%: a big metro is not a garrison town
        acs["0500000US01001"]["B23025_006"] = 3500
        out = military.cities_block(self.loc, sites, rng, acs)
        self.assertEqual(out["A"]["tier"], "garrison")  # 0.35%, at least 2,000
        self.assertNotIn("B", out)  # range covers its hex, but 1,500 and 0.15% fail (b)
        acs["0500000US01003"]["B23025_006"] = 20000
        out = military.cities_block(self.loc, sites, rng, acs)
        self.assertEqual(out["B"]["installations"], ["range"])

    def test_naval_by_tier_weight_not_parcel_count(self):
        sites = [inst("navy1", "01001", component="navy"), inst("navy2", "01001", component="navy"),
                 inst("navy3", "01001", component="navy"), inst("big", "01001", component="air_force", tier="major")]
        out = military.cities_block(self.loc, sites, [], self.acs(**{"01001": (500, 10000)}))
        self.assertFalse(out["A"]["naval"])  # three small Navy parcels do not outweigh one major Air Force base

    def test_missing_acs_is_not_zero(self):
        out = military.cities_block(self.loc, [inst("fort", "01001")], [], {})
        self.assertEqual(out, {})


class TierRuleTest(unittest.TestCase):
    def test_tiers(self):
        t = military._tier
        self.assertEqual(t(600.0, "army_guard", False), "base")  # Guard is never major
        self.assertEqual(t(5.0, "air_guard", False), "site")
        self.assertEqual(t(300.0, "army_reserve", False), "base")
        self.assertEqual(t(16.4, "navy", True), "major")  # flagship on small land
        self.assertEqual(t(16.4, "navy", False), "base")
        self.assertEqual(t(150.0, "army", False), "major")
        self.assertEqual(t(0.05, "navy", False), "site")  # a parcel
        self.assertEqual(t(None, "air_force", False), "site")

    def test_flagships_are_active_duty(self):
        self.assertTrue(all(c in military.ACTIVE_DUTY for _, c in military.FLAGSHIPS))

    def site(self, name, comp, area, cell=1, x=0.0, y=0.0, curated=False):
        return {"id": name, "name": name, "component": comp, "area_km2": area, "cell": cell, "x": x, "y": y,
                "curated": curated, "joint": False}

    def test_fold_small_parcels_into_the_nearest_installation(self):
        out = [self.site("Naval Station", "navy", 16.4, x=0, curated=True), self.site("Shipyard", "navy", 1.9, x=20),
               self.site("St Helena", "navy", 0.05, x=19), self.site("School", "navy", 0.05, x=1),
               self.site("Guard armory", "army_guard", 0.05, x=1), self.site("Lonely", "navy", 0.2, cell=2)]
        kept, n = military._fold(out)
        names = {i["name"]: i for i in kept}
        self.assertEqual(n, 2)
        self.assertEqual(names["Shipyard"]["parts"], ["St Helena"])
        self.assertEqual(names["Naval Station"]["parts"], ["School"])
        self.assertIn("Guard armory", names)  # another component: not folded
        self.assertIn("Lonely", names)  # no larger installation in its hex
        self.assertAlmostEqual(names["Shipyard"]["area_km2"], 1.95)

    def test_aliases_merge_one_range(self):
        out = [self.site("The Barry M Goldwater Air Force Range", "air_force", 4250.0, cell=1),
               self.site("Barry Goldwater Range", "marines", 2810.0, cell=9)]
        kept, n = military._fold(out)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["components"], ["air_force", "marines"])
        self.assertEqual(kept[0]["area_km2"], 7060.0)


def good_doc():
    return {
        "version": 1, "vintage": {"installations": "x"}, "sources": ["mirta"], "rule": "r",
        "components": military.COMPONENTS,
        "installations": [{"id": "fort-usa", "name": "Fort", "component": "army", "joint": False, "status": "act",
                           "tier": "major", "area_km2": 500.0, "cells": [0, 1], "x": 5.0, "y": 5.0, "cell": 0,
                           "county": "01001", "cbsa": "10000", "state": "AL"}],
        "ranges": [{"id": "fort-usa", "name": "Fort", "component": "army", "area_km2": 500.0, "cells": [0, 1],
                    "state": "AL", "installation": "fort-usa"}],
        "states": {"AL": {"personnel": {"active": {"army": 10, "total": 10}, "guard_reserve": {"total": None},
                                        "civilian": {"total": 3}, "total": 13},
                          "spending": {"total": 1.5e9, "pct_gdp": 0.02, "top_counties": [{"name": "A", "county": "01001"}]}}},
        "national": {"veterans": 1},
        "cities": {"10000": {"tier": "fortress", "armed_forces": 100, "share": 0.05, "naval": False,
                             "installations": ["fort-usa"]}},
    }


class MilitaryCheckTest(unittest.TestCase):
    def run_check(self, doc):
        web, _ = dataset()
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("world.json", "cities.json"):
                (Path(tmp) / name).write_text(json.dumps(web[name]))
            if doc is not None:
                (Path(tmp) / "military.json").write_text(json.dumps(doc))
            return military.check(Path(tmp))

    def test_missing_file_is_a_note(self):
        self.assertEqual([lv for lv, _ in self.run_check(None)], ["NOTE"])

    def test_valid_file_passes(self):
        lines = self.run_check(good_doc())
        self.assertFalse([m for lv, m in lines if lv in ("FAIL", "WARN")], lines)

    def test_violations_fail(self):
        cases = {
            "ocean cell": lambda d: d["installations"][0].update(cell=2),
            "component": lambda d: d["installations"][0].update(component="space"),
            "zero area": lambda d: d["installations"][0].update(area_km2=0),
            "empty cells": lambda d: d["installations"][0].update(cells=[]),
            "county": lambda d: d["installations"][0].update(county="99999"),
            "range cells": lambda d: d["ranges"][0].update(cells=[5]),
            "pct_gdp": lambda d: d["states"]["AL"]["spending"].update(pct_gdp=2.5),
            "personnel": lambda d: d["states"]["AL"]["personnel"]["active"].update(army=-1),
            "city tier": lambda d: d["cities"]["10000"].update(tier="castle"),
            "city id": lambda d: d["cities"].update({"99999": d["cities"]["10000"]}),
            "dup id": lambda d: d["installations"].append(dict(d["installations"][0])),
        }
        for what, change in cases.items():
            doc = copy.deepcopy(good_doc())
            change(doc)
            with self.subTest(what=what):
                self.assertTrue([m for lv, m in self.run_check(doc) if lv == "FAIL"], what)


class RenameTest(unittest.TestCase):
    def test_2025_army_renames(self):
        n = military.current_name
        self.assertEqual(n("Fort Cavazos"), "Fort Hood")
        self.assertEqual(n("NG Fort Barfoot MTC & FMS 15"), "NG Fort Pickett MTC & FMS 15")
        self.assertEqual(n("Fort Gregg-Adams"), "Fort Lee")
        self.assertEqual(n("Seymour Johnson Air Force Base"), "Seymour Johnson Air Force Base")  # not 'Fort Johnson'
        self.assertEqual(n("Fort Bragg"), "Fort Bragg")

    def test_apply_keeps_mirta_name_and_renames_parts(self):
        rows = [{"name": "Fort Johnson", "parts": ["Fort Johnson Annex"]}, {"name": "Fort Bliss"}]
        self.assertEqual(military.apply_renames(rows), 1)
        self.assertEqual(rows[0], {"name": "Fort Polk", "name_mirta": "Fort Johnson", "parts": ["Fort Polk Annex"]})
        self.assertNotIn("name_mirta", rows[1])


if __name__ == "__main__":
    unittest.main()
