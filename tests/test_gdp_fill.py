"""BEA suppression recovery (build/yields.py) and the valley-floor relief rule (build/world.py)."""

import unittest

from pipeline.build.world import is_valley_floor
from pipeline.build.yields import fill_suppressed, gdp_profile


class FillSuppressedTest(unittest.TestCase):
    def test_exact_recovery_from_parent(self):
        # Finance (51) withheld, its parent FIRE (50) and sibling real estate (56) published.
        out = fill_suppressed({"50": 100.0, "51": None, "56": 60.0})
        self.assertEqual(out["51"], 40)

    def test_parent_recovered_from_children(self):
        out = fill_suppressed({"50": None, "51": 10.0, "56": 5.0})
        self.assertEqual(out["50"], 15)

    def test_two_unknowns_stay_unknown_without_reference(self):
        out = fill_suppressed({"50": 100.0, "51": None, "56": None})
        self.assertIsNone(out["51"])
        self.assertIsNone(out["56"])

    def test_apportion_by_reference(self):
        est = set()
        out = fill_suppressed({"50": 100.0, "51": None, "56": None}, {"51": 3.0, "56": 1.0}, est)
        self.assertEqual((out["51"], out["56"]), (75, 25))
        self.assertEqual(est, {"51", "56"})

    def test_profile_reports_undisclosed(self):
        lines = {"1": 1000.0, "3": 100.0, "6": None}
        g = gdp_profile(lines, 2024)
        self.assertEqual(g["total"], 1000.0)
        self.assertEqual(g["by_industry"]["agriculture"], 100.0)
        self.assertIsNone(g["by_industry"]["mining"])
        self.assertEqual(g["undisclosed"], 900)


class ValleyFloorTest(unittest.TestCase):
    def test_flat_lowland_with_a_range_at_the_edge(self):
        self.assertTrue(is_valley_floor([20, 25, 30, 35, 40, 45, 50, 900, 1500, 2200]))

    def test_real_mountains(self):
        self.assertFalse(is_valley_floor([800, 1100, 1400, 1700, 2000, 2300, 2600, 2900]))

    def test_too_few_samples(self):
        self.assertFalse(is_valley_floor([10, 20, 1500]))


if __name__ == "__main__":
    unittest.main()
