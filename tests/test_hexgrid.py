"""Hex grid invariants and the golden cases shared with web/js/hex.js."""

import json
import math
import unittest
from pathlib import Path

from pipeline.core.hexgrid import HexGrid

FIXTURE = Path(__file__).parent / "fixtures" / "hex_cases.json"


class HexGridTest(unittest.TestCase):
    grid = HexGrid(25.0, 117, 90)  # the production map size

    def test_center_roundtrip(self):
        g = self.grid
        for i in range(g.size):
            self.assertEqual(g.cell_at(*g.center(*g.col_row(i))), i)

    def test_index_col_row(self):
        g = self.grid
        for i in (0, 1, g.cols - 1, g.cols, g.size - 1):
            self.assertEqual(g.index(*g.col_row(i)), i)

    def test_neighbour_symmetry_and_distance(self):
        g = self.grid
        step = math.sqrt(3) * g.radius
        for i in range(g.size):
            ns = g.neighbours(i)
            self.assertLessEqual(len(ns), 6)
            self.assertEqual(len(set(ns)), len(ns))
            for j in ns:
                self.assertIn(i, g.neighbours(j))
                self.assertAlmostEqual(math.dist(g.center(*g.col_row(i)), g.center(*g.col_row(j))), step, places=6)
        interior = g.index(5, 5)
        self.assertEqual(len(g.neighbours(interior)), 6)

    def test_outside_is_minus_one(self):
        g = self.grid
        self.assertEqual(g.cell_at(-100, -100), -1)
        self.assertEqual(g.cell_at(1e6, 10), -1)

    def test_nearest_center(self):
        """cell_at picks the cell whose centre is nearest (away from exact ties)."""
        g = HexGrid(10.0, 8, 8)
        for k in range(2000):
            x, y = (k * 7.31) % 120, (k * 3.77) % 110
            i = g.cell_at(x, y)
            if i < 0:
                continue
            d = math.dist((x, y), g.center(*g.col_row(i)))
            best = min(math.dist((x, y), g.center(*g.col_row(j))) for j in range(g.size))
            self.assertAlmostEqual(d, best, places=9)

    def test_js_rounding_on_ties(self):
        """Exact .5 ties round up like JS Math.round, not to even like Python's round()."""
        g = HexGrid(25.0, 12, 9)
        x, y = -math.sqrt(3) * 25 / 2, 75.0  # the left edge of cell (0, 2): round() would say "outside"
        self.assertEqual(g.cell_at(x, y), g.index(0, 2))

    def test_golden_fixture(self):
        data = json.loads(FIXTURE.read_text())
        g = HexGrid(**data["grid"])
        ties = 0
        for case in data["cell_at"]:
            self.assertEqual(g.cell_at(case["x"], case["y"]), case["cell"], case)
            ties += case["kind"] == "tie"
        self.assertGreater(ties, 0, "fixture should include rounding ties")
        for case in data["center"]:
            self.assertEqual(g.center(*g.col_row(case["cell"])), (case["x"], case["y"]))
        for case in data["neighbours"]:
            self.assertEqual(g.neighbours(case["cell"]), case["neighbours"])


if __name__ == "__main__":
    unittest.main()
