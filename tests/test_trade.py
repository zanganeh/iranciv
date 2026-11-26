"""trade builder: zone merging, interstate-only aggregation, road paths and its check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import trade
from pipeline.core.hexgrid import HexGrid

GRID = HexGrid(10.0, 12, 10)


def faf_fixture():
    # One metro split by a state line (CSA 100 in states 01 and 02), each state's rest, and Alaska.
    zones = {
        "011": {"faf": "011", "state_fips": "01", "state": "AA", "cfsma": "100", "name": "Twin City, AA-BB",
                "short": "Twin", "counties": ["01001"], "delineation_title": "Twin City, AA-BB"},
        "019": {"faf": "019", "state_fips": "01", "state": "AA", "cfsma": "99999", "name": "Remainder of Aland",
                "short": None, "counties": []},
        "041": {"faf": "041", "state_fips": "04", "state": "BB", "cfsma": "100", "name": "Twin City, AA-BB",
                "short": "Twin", "counties": ["04001"], "delineation_title": "Twin City, AA-BB"},
        "049": {"faf": "049", "state_fips": "04", "state": "BB", "cfsma": "99999", "name": "Remainder of Bland",
                "short": None, "counties": []},
        "020": {"faf": "020", "state_fips": "02", "state": "AK", "cfsma": "99999", "name": "Alaska", "short": None,
                "counties": []},
    }
    m = lambda v, t=1.0: {"truck": [v, t, 1.0]}  # noqa: E731
    pairs = [
        {"a": "011", "b": "020", "modes": m(900), "commodities": [["43", 900]]},   # Alaska: off the map
        {"a": "011", "b": "041", "modes": m(500), "commodities": [["43", 500]]},   # inside the merged twin
        {"a": "011", "b": "019", "modes": m(300), "commodities": [["01", 300]]},   # same state: not a route
        {"a": "011", "b": "049", "modes": m(40), "commodities": [["02", 40]]},     # interstate
        {"a": "019", "b": "041", "modes": {"rail": [60, 2, 1], "truck": [20, 1, 1]}, "commodities": [["15", 80]]},
        {"a": "019", "b": "049", "modes": m(10), "commodities": [["02", 10]]},
    ]
    return {"zones": zones, "pairs": pairs, "commodities": {"01": "Animals", "02": "Grains", "15": "Coal", "43": "Mixed"},
            "zone_totals": {"011": {"within": 5.0}, "041": {"within": 7.0}}}


def world_fixture():
    owner = [0] * GRID.size
    county = [-1] * GRID.size
    counties = ["01001", "01002", "04001", "04002"]
    for i in range(GRID.size):
        col, row = GRID.col_row(i)
        county[i] = (0 if col < 3 else 1) if col < 6 else (2 if col < 9 else 3)
    return {"grid": GRID.to_dict(), "owner": owner, "county": county, "counties": counties}


CITIES = [{"id": "c1", "name": "Twin", "counties": ["01001", "04001"], "cell": GRID.index(2, 2), "x": 1.0, "y": 2.0},
          {"id": "c2", "name": "Bville", "counties": ["04002"], "cell": GRID.index(10, 8), "x": 5.0, "y": 6.0}]


class ZonesTest(unittest.TestCase):
    def setUp(self):
        self.faf = faf_fixture()
        self.zones, self.zone_of = trade.build_zones(self.faf, world_fixture(), CITIES, {"01": "AA", "04": "BB"})

    def test_twins_merge_and_rest_of_state(self):
        self.assertEqual(set(self.zones), {"011", "019", "049"})
        twin = self.zones["011"]
        self.assertEqual(twin["faf"], ["011", "041"])
        self.assertEqual(twin["states"], ["AA", "BB"])
        self.assertEqual(twin["kind"], "metro")
        self.assertEqual(self.zone_of["041"], "011")
        self.assertEqual(self.zones["019"]["kind"], "rest_of_state")
        self.assertEqual(self.zones["019"]["name"], "Rest of Aland")
        self.assertEqual(self.zones["019"]["counties"], 1)  # 01002 only

    def test_cities_and_anchors(self):
        self.assertEqual(self.zones["011"]["cities"], ["c1"])
        self.assertEqual(self.zones["011"]["anchor"]["city"], "c1")
        self.assertEqual(self.zones["049"]["cities"], ["c2"])
        anchor = self.zones["019"]["anchor"]  # no city: the middle of its cells
        self.assertIsNone(anchor["city"])
        self.assertEqual(world_fixture()["county"][anchor["cell"]], 1)

    def test_only_interstate_part_pairs_count(self):
        parts_state = {k: z["state_fips"] for k, z in self.faf["zones"].items()}
        route, partner = trade.aggregate(self.faf, parts_state, self.zone_of)
        self.assertNotIn(("011", "011"), route)
        # 011-049 interstate (40) and 041-019 interstate (80); the AA-only 011-019 flow (300) is left out.
        self.assertEqual(sum(v[0] for v in route[("011", "049")]["modes"].values()), 40)
        self.assertEqual(sum(v[0] for v in route[("011", "019")]["modes"].values()), 80)
        self.assertEqual(route[("011", "019")]["sctg"], {"15": 80})
        # Partners count every flow, same state included.
        self.assertEqual(sum(partner["011"]["019"].values()), 380)


class PathTest(unittest.TestCase):
    def test_path_prefers_roads_and_joins_ends(self):
        owner = [0] * GRID.size
        a, b = GRID.index(1, 1), GRID.index(8, 1)
        road = trade.road_graph({"road_classes": [{"id": "interstate"}, {"id": "major"}], "rail": [],
                                 "roads": [[min(u, v), max(u, v), 0, None]
                                           for u, v in zip(range(a, b), range(a + 1, b + 1))]})
        path = trade.shortest_path(GRID, owner, road, a, b)
        self.assertEqual(path, list(range(a, b + 1)))
        far = trade.shortest_path(GRID, owner, {}, GRID.index(0, 0), GRID.index(11, 9))
        self.assertEqual(far[0], GRID.index(0, 0))
        self.assertEqual(far[-1], GRID.index(11, 9))
        for u, v in zip(far, far[1:]):
            self.assertIn(v, GRID.neighbours(u))


def good_doc():
    a, b = GRID.index(1, 1), GRID.index(3, 1)
    return {
        "version": 1, "vintage": "FAF", "year": 2024, "sources": ["faf5"], "rule": "r",
        "modes": [{"id": m, "name": n} for m, n in trade.MODES], "commodities": {"43": "Mixed"},
        "zones": [{"id": z, "faf": [z], "name": z, "short": z, "kind": "metro", "states": [s], "cities": [],
                   "anchor": {"cell": c, "x": 0, "y": 0, "city": None}} for z, s, c in (("011", "AA", a), ("049", "BB", b))],
        "routes": [{"a": "011", "b": "049", "rank": 1, "value_musd": 10.0, "tons_k": 1.0, "tmiles_m": 1.0,
                    "modes": {"truck": 1.0}, "top_mode": "truck", "top_commodity": "43", "dist_km": 300,
                    "path": [a, a + 1, b]}],
    }


class CheckTest(unittest.TestCase):
    def run_check(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "world.json").write_text(json.dumps({"grid": GRID.to_dict(), "owner": [0] * GRID.size}))
            if doc is not None:
                (web / "trade.json").write_text(json.dumps(doc))
            return {lv for lv, _ in trade.check(web)}

    def test_missing_is_note_good_passes(self):
        self.assertEqual(self.run_check(None), {"NOTE"})
        self.assertNotIn("FAIL", self.run_check(good_doc()))

    def test_violations_fail(self):
        cases = {
            "unknown zone": lambda d: d["routes"][0].update(b="999"),
            "broken path": lambda d: d["routes"][0].update(path=[d["routes"][0]["path"][0], GRID.size - 1]),
            "shares": lambda d: d["routes"][0].update(modes={"truck": 0.5}),
            "commodity": lambda d: d["routes"][0].update(top_commodity="99"),
            "zero value": lambda d: d["routes"][0].update(value_musd=0),
            "anchor": lambda d: d["zones"][0]["anchor"].update(cell=-3),
        }
        for what, change in cases.items():
            doc = good_doc()
            change(doc)
            with self.subTest(what):
                self.assertIn("FAIL", self.run_check(doc))


if __name__ == "__main__":
    unittest.main()
