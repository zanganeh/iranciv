"""Population growth (pipeline/build/growth.py), the PEP source, and the sources' --offline fetch."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.build import growth
from pipeline.core import fetch
from pipeline.sources.acs import AcsMetro1Yr
from pipeline.sources.pep import PepPopulation

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
# Chicago-Naperville-Elgin (16980), PEP vintage 2025. On the 2023 borders (Kenosha County, WI,
# is no longer part of it) the metro grew 2021→2025. Comparing ACS 2025 (9,433,437, new borders)
# with ACS 2021 (9,510,390, old borders incl. Kenosha) showed -0.81%: the bug this replaces.
CHICAGO_COUNTIES = ["17031", "17037", "17043", "17063", "17089", "17093", "17097", "17111", "17197",
                    "18073", "18089", "18111", "18127"]
PEP = {
    "year": 2025,
    "years": YEARS,
    "nation": [331578104, 332100166, 333996304, 336755052, 340003797, 341784857],
    "states": {"17": [12800000, 12700000, 12600000, 12550000, 12700000, 12710000]},
    "cbsa": {
        "16980": {"name": "Chicago-Naperville-Elgin, IL-IN", "counties": CHICAGO_COUNTIES,
                  "pop": [9435971, 9365495, 9303151, 9335921, 9411198, 9434123]},
        "99999": {"name": "Boomtown", "counties": ["48001"], "pop": [100, 100, 110, 120, 130, 140]},
    },
}


class RateTest(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(growth.rate(110, 100), 0.1)
        self.assertEqual(growth.rate(95, 100), -0.05)
        self.assertIsNone(growth.rate(None, 100))
        self.assertIsNone(growth.rate(100, 0))
        self.assertIsNone(growth.rate(126, 100), "more than 25% is an artefact, not growth")
        self.assertIsNone(growth.rate(70, 100))
        self.assertEqual(growth.rate(125, 100), 0.25)


class GrowthTest(unittest.TestCase):
    def setUp(self):
        self.g = growth.Growth(PEP, 2025)

    def test_window(self):
        self.assertEqual(self.g.years, [2021, 2025])
        self.assertEqual(self.g.nation(), 0.0292)
        self.assertEqual(self.g.state("17"), round(12710000 / 12700000 - 1, 4))
        self.assertIsNone(self.g.state("99"))

    def test_chicago_is_like_for_like(self):
        g, why = self.g.cbsa("16980", CHICAGO_COUNTIES)
        self.assertIsNone(why)
        self.assertEqual(g, 0.0073)  # growth on one footprint, not the -0.81% of mixed borders
        self.assertGreater(g, 0)

    def test_county_list_must_match_delineation(self):
        with_kenosha = CHICAGO_COUNTIES + ["55059"]
        self.assertEqual(self.g.cbsa("16980", with_kenosha), (None, "estimates use a different county list"))
        self.assertEqual(self.g.cbsa("16980", None)[0], 0.0073)  # no delineation list: trust PEP's

    def test_missing_and_capped(self):
        self.assertEqual(self.g.cbsa("12345", ["01001"]), (None, "not in population estimates"))
        g, why = self.g.cbsa("99999", ["48001"])  # +40%
        self.assertIsNone(g)
        self.assertIn("25%", why)

    def test_window_follows_acs_year(self):
        self.assertEqual(growth.Growth(PEP, 2024).years, [2021, 2024])
        with mock.patch("builtins.print"):
            g = growth.Growth(PEP, 2026)  # PEP behind ACS: ends at PEP's last year (check flags it)
        self.assertEqual(g.years, [2021, 2025])

    def test_no_estimates(self):
        g = growth.Growth(None, 2025)
        self.assertIsNone(g.years)
        self.assertIsNone(g.nation())
        self.assertEqual(g.cbsa("16980", CHICAGO_COUNTIES), (None, "no population estimates"))


class SourceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name)
        for p in (mock.patch.object(fetch, "RAW", self.raw), mock.patch("pipeline.core.source.RAW", self.raw)):
            p.start()
            self.addCleanup(p.stop)

    def put(self, rel: str, text: str, encoding="utf-8"):
        f = self.raw / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(text.encode(encoding))


class PepSourceTest(SourceTestCase):
    def setUp(self):
        super().setUp()
        cols = ",".join(f"POPESTIMATE{y}" for y in YEARS)
        states = [f"SUMLEV,REGION,DIVISION,STATE,NAME,ESTIMATESBASE2020,{cols},NPOPCHG_2020",
                  "010,0,0,00,United States,1," + ",".join(str(v) for v in PEP["nation"]) + ",0",
                  "020,1,0,00,Northeast Region,1,1,1,1,1,1,1,0"]
        states += [f"040,1,1,{i:02d},State {i},1,{1000 + i},{1100 + i},1,1,1,{1200 + i},0" for i in range(1, 57)]
        cbsa = [f"CBSA,MDIV,STCOU,NAME,LSAD,ESTIMATESBASE2020,{cols},NPOPCHG2020",
                '16980,,,"Chicago-Naperville-Elgin, IL-IN",Metropolitan Statistical Area,1,'
                + ",".join(str(v) for v in PEP["cbsa"]["16980"]["pop"]) + ",0",
                '16980,16984,,"Chicago-Naperville-Schaumburg, IL",Metropolitan Division,1,1,1,1,1,1,1,0',
                '16980,16984,17031,"Cook County, IL",County or equivalent,1,1,1,1,1,1,1,0',
                '16980,29414,18089,"Lake County, IN",County or equivalent,1,1,1,1,1,1,1,0',
                '15860,,,"Cañon City, CO",Micropolitan Statistical Area,1,1,2,3,4,5,6,0',
                '15860,,08043,"Fremont County, CO",County or equivalent,1,1,2,3,4,5,6,0']
        cbsa += [f'{20000 + i},,,"Area {i}, TX",Micropolitan Statistical Area,1,1,1,1,1,1,1,0' for i in range(900)]
        self.put("pep_population/2025/NST-EST2025-ALLDATA.csv", "\n".join(states) + "\n")
        self.put("pep_population/2025/cbsa-est2025-alldata.csv", "\n".join(cbsa) + "\n", "latin-1")
        self.src = PepPopulation()

    def test_process(self):
        out = self.src.process()
        self.assertEqual(out["year"], 2025)
        self.assertEqual(out["years"], YEARS)
        self.assertEqual(out["nation"], PEP["nation"])
        self.assertEqual(out["states"]["06"], [1006, 1106, 1, 1, 1, 1206])
        chi = out["cbsa"]["16980"]
        self.assertEqual(chi["pop"], PEP["cbsa"]["16980"]["pop"])
        self.assertEqual(chi["counties"], ["17031", "18089"])  # counties under its divisions; no division rows
        self.assertEqual(out["cbsa"]["15860"]["name"], "Cañon City, CO")  # Latin-1 file
        self.assertEqual(len(out["_files"]), 2)

    def test_offline_fetch_uses_cache_without_discovery(self):
        with mock.patch.object(fetch, "OFFLINE", True), \
                mock.patch.object(PepPopulation, "latest_vintage", side_effect=AssertionError("no discovery offline")), \
                mock.patch("builtins.print"):
            self.src.fetch()  # everything cached: no network, no error
            (self.raw / "pep_population/2025/NST-EST2025-ALLDATA.csv").unlink()
            with self.assertRaises(FileNotFoundError):  # incomplete vintage: nothing usable cached
                self.src.fetch()

    def test_rejects_html_saved_as_csv(self):
        self.put("pep_population/2025/NST-EST2025-ALLDATA.csv", "<html>gone</html>\n")
        with mock.patch.object(fetch, "OFFLINE", True), mock.patch("builtins.print"), \
                self.assertRaises(RuntimeError):
            self.src.fetch()
        self.assertFalse((self.raw / "pep_population/2025/NST-EST2025-ALLDATA.csv").exists())


class AcsOfflineFetchTest(SourceTestCase):
    def setUp(self):
        super().setUp()
        self.src = AcsMetro1Yr()
        for year, tables in ((2024, self.src.tables), (2025, self.src.tables[:2])):
            for t in tables:
                self.put(f"acs_1yr/{year}/{t}.dat", "GEO_ID|X\n")

    def test_offline_uses_newest_complete_cached_year(self):
        with mock.patch.object(fetch, "OFFLINE", True), \
                mock.patch.object(AcsMetro1Yr, "latest_year", side_effect=AssertionError("no discovery offline")), \
                mock.patch("builtins.print") as out:
            self.src.fetch()
        self.assertIn("2024", " ".join(str(c) for c in out.call_args_list))


if __name__ == "__main__":
    unittest.main()
