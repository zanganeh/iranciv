"""infrastructure builder: hex rasterisation of lines, site placement rules and its check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import infrastructure as infra
from pipeline.core.hexgrid import HexGrid

GRID = HexGrid(10.0, 12, 10)


class IdentityProj:
    """lon/lat are already map km in these tests."""

    def __call__(self, lon, lat):
        return lon, lat


def centre(cell):
    return GRID.center(*GRID.col_row(cell))


class HexLineTest(unittest.TestCase):
    def test_walk_steps_are_neighbours_and_ends_are_kept(self):
        for a, b in [(0, GRID.size - 1), (13, 58), (5, 5), (40, 41), (GRID.index(11, 0), GRID.index(0, 9))]:
            path = infra.hex_walk(GRID, a, b)
            self.assertEqual(path[0], a)
            self.assertEqual(path[-1], b)
            self.assertEqual(len(path), infra.hex_distance(GRID, a, b) + 1)
            for u, v in zip(path, path[1:]):
                self.assertIn(v, GRID.neighbours(u))

    def test_straighten_drops_corner_clips(self):
        # Row 2 → row 3 → row 2 along a row boundary: the row-3 cell only clips a corner.
        a, b, c = GRID.index(2, 2), GRID.index(2, 3), GRID.index(3, 2)
        self.assertIn(c, GRID.neighbours(a))
        self.assertEqual(infra.straighten(GRID, [a, b, c]), [a, c])

    def test_horizontal_line_along_row_boundary_does_not_zigzag(self):
        # y halfway between rows 2 and 3: plain sampling alternates between the two rows.
        y = (centre(GRID.index(0, 2))[1] + centre(GRID.index(0, 3))[1]) / 2
        runs = infra.sample_cells(GRID, [(1.0, y), (180.0, y)])
        links = {l for run in runs for l in infra.links_of(GRID, run)}
        cells = {c for l in links for c in l}
        rows = [GRID.col_row(c)[1] for c in cells]
        self.assertGreater(len(links), 5)
        # Each column is crossed once: no up-down sawtooth.
        self.assertLessEqual(len(links), len(cells) - 1)
        self.assertTrue(set(rows) <= {2, 3})

    def test_keep_breaks_runs(self):
        y = centre(GRID.index(0, 4))[1]
        runs = infra.sample_cells(GRID, [(1.0, y), (180.0, y)], keep=lambda x, _: not 80 < x < 110)
        self.assertEqual(len(runs), 2)


class RoadsTest(unittest.TestCase):
    def test_classes_names_and_land_rules(self):
        owner = [0] * GRID.size
        foreign = GRID.index(9, 5)
        owner[foreign] = -2
        y = centre(GRID.index(0, 5))[1]
        y2 = centre(GRID.index(0, 7))[1]
        ne = {"roads": [
            {"level": "Interstate", "type": "Major Highway", "name": "80", "expressway": 1, "scalerank": 3,
             "lines": [[(1.0, y), (190.0, y)]]},
            {"level": "Federal", "type": "Major Highway", "name": "30", "expressway": 0, "scalerank": 5,
             "lines": [[(1.0, y), (60.0, y)], [(1.0, y2), (100.0, y2)]]},
            {"level": "State", "type": "Secondary Highway", "name": "9", "expressway": 0, "scalerank": 8,
             "lines": [[(1.0, centre(GRID.index(0, 1))[1]), (150.0, centre(GRID.index(0, 1))[1])]]},
        ]}
        roads = infra.build_roads(GRID, IdentityProj(), owner, ne)
        pairs = [(a, b) for a, b, *_ in roads]
        self.assertEqual(len(pairs), len(set(pairs)))
        for a, b, cls, name in roads:
            self.assertLess(a, b)
            self.assertIn(b, GRID.neighbours(a))
            self.assertNotIn(foreign, (a, b))
            self.assertEqual(name, {0: "I-80", 1: "US 30"}[cls])
        row5 = [r for r in roads if GRID.col_row(r[0])[1] == 5 and GRID.col_row(r[1])[1] == 5]
        self.assertTrue(row5 and all(r[2] == 0 for r in row5), "Interstate wins over the US route on shared links")
        self.assertTrue(any(r[2] == 1 for r in roads))
        self.assertFalse(any(GRID.col_row(r[0])[1] == 1 for r in roads), "low-rank secondary roads are left out")

    def test_route_name_order(self):
        self.assertEqual(min(["I-295", "I-95", "I-5"], key=infra._route_key), "I-5")
        self.assertEqual(infra._route_name("Interstate", "I-5"), "I-5")
        self.assertIsNone(infra._route_name("State", None))


def write_world(web, owner=None):
    owner = owner if owner is not None else [0] * GRID.size
    (web / "world.json").write_text(json.dumps({
        "grid": GRID.to_dict(), "owner": owner, "counties": [{"geoid": "01001", "name": "A"}]}))
    (web / "cities.json").write_text(json.dumps([{"id": "10100"}]))


def good_doc():
    a = GRID.index(3, 3)
    b = GRID.neighbours(a)[0]
    x, y = centre(a)
    return {
        "version": 1, "vintage": {"roads": "v"}, "sources": ["natural_earth_roads"],
        "road_classes": infra.ROAD_CLASSES, "rail_tiers": infra.RAIL_TIERS,
        "roads": [[min(a, b), max(a, b), 0, "I-1"]], "rail": [[min(a, b), max(a, b), 1]],
        "ports": [{"id": "1", "name": "P", "tons": 5, "domestic": None, "foreign": 0, "imports": None, "exports": None,
                   "rank": 1, "x": x, "y": y, "cell": a, "county": "01001", "cbsa": "10100"}],
        "airports": [{"id": "AAA", "hub": "L", "enplanements": 10, "enplanements_prev": None, "cargo_lbs": None,
                      "x": x + 3, "y": y, "cell": a, "county": None, "cbsa": None}],
    }


class CheckTest(unittest.TestCase):
    def run_check(self, doc, owner=None):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            write_world(web, owner)
            if doc is not None:
                (web / "infrastructure.json").write_text(json.dumps(doc))
            return infra.check(web)

    def levels(self, lines):
        return {lv for lv, _ in lines}

    def test_missing_file_is_a_note(self):
        lines = self.run_check(None)
        self.assertEqual(self.levels(lines), {"NOTE"})

    def test_good_file_passes(self):
        lines = self.run_check(good_doc())
        self.assertNotIn("FAIL", self.levels(lines), lines)
        self.assertIn("PASS", self.levels(lines))

    def test_violations_fail(self):
        def non_neighbour(d):
            d["roads"][0][1] = d["roads"][0][0] + 5

        def dup(d):
            d["rail"].append(list(d["rail"][0]))

        def water_site(d):
            d["ports"][0]["cell"] = 0

        def bad_cbsa(d):
            d["airports"][0]["cbsa"] = "99999"

        def missing_key(d):
            del d["airports"]

        owner = [0] * GRID.size
        owner[0] = -1
        for change in (non_neighbour, dup, water_site, bad_cbsa, missing_key):
            doc = good_doc()
            change(doc)
            with self.subTest(change.__name__):
                self.assertIn("FAIL", self.levels(self.run_check(doc, owner)))


if __name__ == "__main__":
    unittest.main()
