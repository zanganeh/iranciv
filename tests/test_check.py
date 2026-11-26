"""pipeline.check on a tiny synthetic v2 dataset (a 3×2 grid)."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from pipeline import check

YIELDS = {"food": 10, "production": 20, "gold": 3.5e9, "science": 5, "culture": None}
ECON = {"population": 1000, "employed": 500, "yields": YIELDS, "jobs": {"agriculture": 10, "mining": None},
        "median_income": 60000, "median_rent": 1200, "home_value": 300000, "unemployment": 0.05, "rent_burden": 0.3,
        "gdp": 3.5e9, "gdp_year": 2024, "growth": 0.031, "growth_years": [2021, 2025]}
HEX_AREA = 1.5 * math.sqrt(3) * 25.0**2


def dataset():
    """Grid 3×2: cells 0, 1, 3, 4 are state 0 (counties 0/1), cell 2 is ocean, cell 5 Canada."""
    owner = [0, 0, -1, 0, 0, -2]
    world = {
        "version": 2, "grid": {"radius": 25.0, "cols": 3, "rows": 2}, "width": 120.0, "height": 60.0, "margin": 0,
        "owner": owner, "county": [0, 0, -1, 1, 1, -1], "elev": [10, 20, -50, 30, 40, 100],
        "water": [0, 0, 1, 0, 0, 0], "relief": [0, 1, 0, 0, 2, 1], "cover": [1, 7, 0, 9, 5, 0],
        "pct": {k: [50 if k == "forest" and o >= 0 else 0 for o in owner] for k in check.PCT_KEYS},
        "tables": {
            "water": [{"id": i, "name": n} for i, n in enumerate(["Land", "Ocean", "Coast", "Lake"])],
            "relief": [{"id": i, "name": n} for i, n in enumerate(["Flat", "Hills", "Mountains", "Peaks"])],
            "cover": [{"id": 0, "key": "none", "name": "None"}]
            + [{"id": i, "key": k, "name": k, "color": "#123456"} for i, k in enumerate(check.COVER_KEYS) if i],
        },
        "states": [{"fips": "01", "abbr": "AL", "name": "Alabama"}, {"fips": "02", "abbr": "AK", "name": "Alaska"}],
        "counties": [{"geoid": "01001", "name": "A County"}, {"geoid": "01003", "name": "B County"}],
        "rivers": [{"name": "Big", "rank": 1, "pts": [1.0, 1.0, 50.0, 30.0]}],
    }
    # Ranks: equal values keep population order; Small has the higher GDP per person (same GDP, fewer people).
    city_ranks = {"population": 1, "gdp": 1, "gdp_per_capita": 2, "growth": 1,
                  **{k: (None if v is None else 1) for k, v in YIELDS.items()}}
    cities = [
        {"id": "10000", "name": "Big", "full": "Big, AL", "states": ["AL"], "metro": True, "capital": True,
         "x": 1.0, "y": 1.0, "cell": 0, **ECON, "counties": ["01001"], "footprint": [0, 1],
         "pop_series": {"2020": 990, "2021": 995, "2025": 1020}, "ranks": city_ranks, "peers": ["10001"]},
        {"id": "10001", "name": "Small", "full": "Small, AL", "states": ["AL"], "metro": False, "capital": False,
         "x": 40.0, "y": 40.0, "cell": 4, **ECON, "population": 10, "counties": ["01003"], "footprint": [3, 4],
         "pop_series": None, "ranks": {k: (None if v is None else (1 if k == "gdp_per_capita" else 2))
                                       for k, v in city_ranks.items()}, "peers": ["10000"]},
    ]
    # Two states, so ranks have an order to check. Yields: AL > AK except culture (null in both).
    ranks = {k: (None if v is None else 1) for k, v in YIELDS.items()}
    small = {k: (None if v is None else v / 2) for k, v in YIELDS.items()}
    land = {k: (0.5 if k == "forest" else 0.0) for k in check.PCT_KEYS}
    energy = {"unit": "MW", "total": 100, "by_type": {"nuclear": 100}, "sites": {"nuclear": 1}, "note": "mapped sites only"}
    states = [{"fips": "01", "abbr": "AL", "name": "Alabama", **ECON, "ranks": ranks,
               "label": {"x": 10, "y": 10, "angle": 0, "size": 12}, "land": land, "energy": energy},
              {"fips": "02", "abbr": "AK", "name": "Alaska", **ECON, "yields": small,
               "ranks": {k: (None if v is None else 2) for k, v in ranks.items()}, "land": None,
               "energy": {**energy, "total": 0, "by_type": {"nuclear": 0}, "sites": {"nuclear": 0}}}]
    nation = {"vintage": "ACS 2025 1-year", "year": 2025, "gdp_vintage": None, "nation": ECON, "industries": {"agriculture": "Agriculture"},
              "yields": [{"key": k, "icon": f"y-{k}", "emoji": "*", "label": k, "color": "#7fbf3f", "unit": "workers", "description": "d"}
                         for k in check.YIELD_KEYS]}
    # Population 2000 + 1000 spread as density over full-land cells → conserves exactly.
    d0, d1 = 1000 / (2 * HEX_AREA), 2000 / (2 * HEX_AREA)
    lenses = {"vintage": "ACS 2024 5-year", "groups": [{"id": "people", "name": "People"}],
              "lenses": [{"id": "density", "group": "people", "name": "Population", "icon": "l-people", "emoji": "*",
                          "unit": "people / km²", "format": "float", "scale": "log", "domain": [1, 2000],
                          "ramp": ["#000000", "#ffffff"], "higher_is": "neutral", "source": "acs_5yr", "description": "d",
                          "values": [d0, d0, None, d1, d1, None]}]}
    sources = [{"id": "acs_5yr", "title": "ACS", "publisher": "Census", "homepage": "h", "license": "pd", "description": "d",
                "used_for": ["lenses"], "vintage": "2024", "files": [{"file": "a", "url": "u", "retrieved": "t", "sha256": "s", "bytes": 1}]}]
    resources = {"types": [{"id": "nuclear", "name": "Nuclear", "category": "energy", "emoji": "*", "icon": "r-n", "color": "#e8d44d",
                            "unit": "MW", "description": "≥ 1 MW", "source": "acs_5yr"}],
                 "items": [{"type": "nuclear", "name": "P", "x": 5.0, "y": 5.0, "cell": 0, "value": 100, "detail": "",
                            "state": "AL", "cbsa": "10000"}],
                 "summary": {"note": "mapped sites only", "national": {"nuclear": {"count": 1, "value": 100, "unit": "MW"}},
                             "states": {"AL": {"nuclear": {"count": 1, "value": 100, "unit": "MW"}}}}}
    county_fields = [{"key": k, "label": k, "format": f, "unit": None, "higher_is": "neutral", "source": "acs_5yr",
                      "description": "d"}
                     for k, f in [("geoid", "text"), ("name", "text"), ("state", "text"), ("population", "int"),
                                  ("land_km2", "km2"), ("density", "float"), ("poverty", "pct"), ("cbsa", "text")]]
    counties = {"vintage": "ACS 2024 5-year", "fields": county_fields, "columns": [f["key"] for f in county_fields],
                "rows": [["01001", "A County", "AL", 1000, 100.0, 10.0, 0.12, "10000"],
                         ["01003", "B County", "AL", 2000, 50.0, 40.0, None, None]]}
    interim = {
        "_world": {"data": {"counties": ["01001", "01003"], "county_samples": [[0] * 7, [0] * 7, [], [1] * 7, [1] * 7, []]}},
        "acs_5yr": {"data": {"geos": {"0500000US01001": {"B01003_001": 1000.0}, "0500000US01003": {"B01003_001": 2000.0}}}},
    }
    return {"world.json": world, "cities.json": cities, "states.json": states, "nation.json": nation,
            "lenses.json": lenses, "sources.json": sources, "resources.json": resources, "counties.json": counties}, interim


class CheckTest(unittest.TestCase):
    def run_check(self, web, interim):
        with tempfile.TemporaryDirectory() as tmp:
            wd, idir = Path(tmp) / "web", Path(tmp) / "interim"
            wd.mkdir()
            idir.mkdir()
            for name, doc in web.items():
                if doc is not None:
                    (wd / name).write_text(json.dumps(doc))
            for name, doc in interim.items():
                (idir / f"{name}.json").write_text(json.dumps(doc))
            return check.run(wd, idir, quiet=True)

    def levels(self, rep, level):
        return [m for lv, m in rep.lines if lv == level]

    def test_clean_dataset_passes(self):
        rep = self.run_check(*dataset())
        self.assertEqual(self.levels(rep, "FAIL"), [])
        self.assertEqual(self.levels(rep, "WARN"), [])
        self.assertTrue(any("density conservation" in m and "100.00%" in m for m in self.levels(rep, "PASS")))

    def test_missing_optional_is_a_note(self):
        web, interim = dataset()
        web["resources.json"] = None
        web["counties.json"] = None
        for s in web["states.json"]:
            s["energy"] = None  # what states.py writes when resources.json is not built
        rep = self.run_check(web, interim)
        self.assertFalse(rep.failed)
        notes = " | ".join(self.levels(rep, "NOTE"))
        self.assertIn("resources.json", notes)
        self.assertIn("counties.json", notes)

    def test_oil_summary_value_is_null(self):
        web, interim = dataset()
        res = web["resources.json"]
        res["types"].append({**res["types"][0], "id": "oil", "unit": "km² play area"})
        res["items"].append({**res["items"][0], "type": "oil", "value": 5000})
        res["summary"]["national"]["oil"] = {"count": 1, "value": None, "unit": "km² play area"}
        res["summary"]["states"]["AL"]["oil"] = {"count": 1, "value": None, "unit": "km² play area"}
        self.assertFalse(self.run_check(web, interim).failed)
        res["summary"]["national"]["oil"]["value"] = 5000
        self.assertTrue(self.run_check(web, interim).failed)

    def test_missing_required_fails(self):
        web, interim = dataset()
        web["states.json"] = None
        self.assertTrue(self.run_check(web, interim).failed)

    def test_v1_world_fails(self):
        web, interim = dataset()
        del web["world.json"]["version"]
        rep = self.run_check(web, interim)
        self.assertTrue(any("version" in m for m in self.levels(rep, "FAIL")))

    def test_contract_violations(self):
        cases = {
            "lens value on water": lambda w: w["lenses.json"]["lenses"][0]["values"].__setitem__(2, 5.0),
            "lens wrong length": lambda w: w["lenses.json"]["lenses"][0]["values"].pop(),
            "owner out of range": lambda w: w["world.json"]["owner"].__setitem__(0, 7),
            "relief on water": lambda w: w["world.json"]["relief"].__setitem__(2, 2),
            "pct over 100": lambda w: w["world.json"]["pct"]["crop"].__setitem__(0, 90),
            "cities unsorted": lambda w: w["cities.json"].reverse(),
            "duplicate city id": lambda w: w["cities.json"][1].__setitem__("id", "10000"),
            "bad yield key": lambda w: w["cities.json"][0].__setitem__("yields", {"food": 1}),
            "unemployment as percent": lambda w: w["states.json"][0].__setitem__("unemployment", 5.0),
            "state order": lambda w: w["states.json"].reverse(),
            "bad resource type": lambda w: w["resources.json"]["items"][0].__setitem__("type", "unicorn"),
            "sources without sha": lambda w: w["sources.json"][0]["files"][0].pop("sha256"),
            "rank too big": lambda w: w["states.json"][0]["ranks"].__setitem__("food", 9),
            "string population": lambda w: w["nation.json"]["nation"].__setitem__("population", "1000"),
            "water above sea level": lambda w: w["world.json"]["elev"].__setitem__(2, 500),
            "state missing econ fields": lambda w: [w["states.json"][0].pop(k) for k in ("gdp", "median_income", "unemployment")],
            "ranks missing yield keys": lambda w: w["states.json"][0].__setitem__("ranks", {"food": 1}),
            "ranks not a permutation": lambda w: w["states.json"][1]["ranks"].__setitem__("food", 1),
            "ranks against yield order": lambda w: (w["states.json"][0]["ranks"].__setitem__("gold", 2),
                                                   w["states.json"][1]["ranks"].__setitem__("gold", 1)),
            "rank on null yield": lambda w: w["states.json"][0]["ranks"].__setitem__("culture", 1),
            "nation missing econ fields": lambda w: [w["nation.json"]["nation"].pop(k) for k in ("population", "gdp", "median_income")],
            "pct lens as percent": lambda w: w["lenses.json"]["lenses"].append(
                {**w["lenses.json"]["lenses"][0], "id": "share", "format": "pct", "scale": "linear",
                 "values": [50.0, 20.0, None, 10.0, 5.0, None]}),
            "city in unknown state": lambda w: w["cities.json"][0].__setitem__("states", ["ZZ"]),
            "metro not a bool": lambda w: w["cities.json"][0].__setitem__("metro", "yes"),
            "capital not a bool": lambda w: w["cities.json"][1].__setitem__("capital", 0),
            "river rank not int": lambda w: w["world.json"]["rivers"][0].__setitem__("rank", "x"),
            "pct on non-US cell": lambda w: w["world.json"]["pct"]["forest"].__setitem__(5, 30),
            "growth over 25%": lambda w: w["cities.json"][0].__setitem__("growth", 0.4),
            "growth as percent": lambda w: w["states.json"][0].__setitem__("growth", 3.1),
            "growth_years reversed": lambda w: w["cities.json"][0].__setitem__("growth_years", [2025, 2021]),
            "growth without years": lambda w: w["nation.json"]["nation"].__setitem__("growth_years", None),
            "city missing growth": lambda w: w["cities.json"][1].pop("growth"),
            "state growth null within a window": lambda w: w["states.json"][0].__setitem__("growth", None),
            "nation growth null within a window": lambda w: w["nation.json"]["nation"].__setitem__("growth", None),
            "growth window ends before nation year": lambda w: [
                o.__setitem__("growth_years", [2021, 2024])
                for o in (*w["cities.json"], *w["states.json"], w["nation.json"]["nation"])],
            "growth windows differ": lambda w: w["cities.json"][1].__setitem__("growth_years", [2020, 2025]),
            # City view
            "footprint without own cell": lambda w: w["cities.json"][0].__setitem__("footprint", [1]),
            "footprint unsorted": lambda w: w["cities.json"][0].__setitem__("footprint", [1, 0]),
            "footprint on water": lambda w: w["cities.json"][0].__setitem__("footprint", [0, 2]),
            "city missing footprint": lambda w: w["cities.json"][0].pop("footprint"),
            "county geoid not a string": lambda w: w["cities.json"][0].__setitem__("counties", [1001]),
            "pop_series negative": lambda w: w["cities.json"][0]["pop_series"].__setitem__("2021", -5),
            "pop_series bad year": lambda w: w["cities.json"][0]["pop_series"].__setitem__("y2", 5),
            "peer is self": lambda w: w["cities.json"][0].__setitem__("peers", ["10000"]),
            "unknown peer": lambda w: w["cities.json"][0].__setitem__("peers", ["99999"]),
            "city ranks missing key": lambda w: w["cities.json"][0]["ranks"].pop("gdp_per_capita"),
            "city ranks against value": lambda w: (w["cities.json"][0]["ranks"].__setitem__("gdp_per_capita", 1),
                                                   w["cities.json"][1]["ranks"].__setitem__("gdp_per_capita", 2)),
            "city rank on null yield": lambda w: w["cities.json"][0]["ranks"].__setitem__("culture", 1),
            # states land / energy
            "land as percent": lambda w: w["states.json"][0]["land"].__setitem__("forest", 50),
            "land missing key": lambda w: w["states.json"][0]["land"].pop("crop"),
            "energy total wrong": lambda w: w["states.json"][0]["energy"].__setitem__("total", 999),
            "energy without note": lambda w: w["states.json"][0]["energy"].pop("note"),
            "energy sites disagree": lambda w: w["states.json"][0]["energy"]["sites"].__setitem__("nuclear", 0),
            # resources state / cbsa / summary
            "resource unknown state": lambda w: w["resources.json"]["items"][0].__setitem__("state", "ZZ"),
            "resource unknown cbsa": lambda w: w["resources.json"]["items"][0].__setitem__("cbsa", "99999"),
            "resource missing cbsa": lambda w: w["resources.json"]["items"][0].pop("cbsa"),
            "summary missing": lambda w: w["resources.json"].pop("summary"),
            "summary count off": lambda w: w["resources.json"]["summary"]["national"]["nuclear"].__setitem__("count", 2),
            "summary value off": lambda w: w["resources.json"]["summary"]["states"]["AL"]["nuclear"].__setitem__("value", 5),
            "summary missing state": lambda w: w["resources.json"]["summary"].__setitem__("states", {}),
            # counties.json
            "county columns vs fields": lambda w: w["counties.json"]["columns"].reverse(),
            "county row too short": lambda w: w["counties.json"]["rows"][0].pop(),
            "county pct as percent": lambda w: w["counties.json"]["rows"][0].__setitem__(6, 12.0),
            "county unknown cbsa": lambda w: w["counties.json"]["rows"][1].__setitem__(7, "99999"),
            "county order": lambda w: w["counties.json"]["rows"].reverse(),
            "county bad format": lambda w: w["counties.json"]["fields"][3].__setitem__("format", "weird"),
        }
        for label, mutate in cases.items():
            web, interim = dataset()
            web = copy.deepcopy(web)
            mutate(web)
            with self.subTest(label):
                self.assertTrue(self.run_check(web, interim).failed, label)

    def test_published_zero_is_a_note(self):
        web, interim = dataset()
        web["cities.json"][0]["yields"] = {**YIELDS, "food": 0}
        interim["acs_1yr"] = {"data": {"cbsa": {"10000": {"C24030_004": 0.0, "C24030_031": 0.0}}}}
        rep = self.run_check(web, interim)
        self.assertEqual(self.levels(rep, "WARN"), [])
        self.assertTrue(any("published as 0" in m for m in self.levels(rep, "NOTE")))

    def test_null_growth_is_fine(self):
        web, interim = dataset()
        web["cities.json"][1] = {**web["cities.json"][1], "growth": None}
        web["cities.json"][1]["ranks"] = {**web["cities.json"][1]["ranks"], "growth": None}
        web["states.json"][1] = {**web["states.json"][1], "growth": None, "growth_years": None}
        rep = self.run_check(web, interim)
        self.assertFalse(rep.failed)
        self.assertTrue(any("cities growth 2021→2025: 1 of 2" in m for m in self.levels(rep, "NOTE")))
        self.assertTrue(any(m.startswith("growth: one window 2021→2025") for m in self.levels(rep, "PASS")))

    def test_quality_warnings(self):
        web, interim = dataset()
        web["cities.json"][1]["cell"] = 0  # shares Big's cell
        web["cities.json"][1]["footprint"] = [0, 3, 4]
        web["lenses.json"]["lenses"][0]["values"][0] *= 3  # breaks conservation
        web["world.json"]["rivers"].append({"name": "Lost", "rank": 5, "pts": [-500.0, -500.0, -400.0, -450.0]})
        web["cities.json"][0]["yields"] = {**YIELDS, "food": 0}
        web["cities.json"][0]["ranks"] = {**web["cities.json"][0]["ranks"], "food": 2}
        web["cities.json"][1]["ranks"] = {**web["cities.json"][1]["ranks"], "food": 1}
        rep = self.run_check(web, interim)
        warns = " | ".join(self.levels(rep, "WARN"))
        self.assertFalse(rep.failed)
        for text in ("density conservation", "more than one city", "outside the map", "exactly 0"):
            self.assertIn(text, warns)

    def test_city_on_water_and_null_lens_share(self):
        web, interim = dataset()
        web["cities.json"][1]["cell"] = 2
        web["cities.json"][1]["footprint"] = [2, 3, 4]
        web["lenses.json"]["lenses"][0]["values"][3] = None
        rep = self.run_check(web, interim)
        warns = " | ".join(self.levels(rep, "WARN"))
        self.assertIn("non-land cells: 1", warns)
        self.assertIn("lens density: 25.0%", warns)

    def test_partial_land_share(self):
        """Cells half covered by county samples count half their area."""
        web, interim = dataset()
        interim["_world"]["data"]["county_samples"][0] = [0, 0, 0, -1, -1, -1, 0]  # 4/7 land
        rep = self.run_check(web, interim)
        self.assertTrue(any("density conservation" in m for m in self.levels(rep, "WARN")))

    def _add_unsampled_county(self, web, interim, pop):
        interim["_world"]["data"]["counties"].append("01005")
        web["world.json"]["counties"].append({"geoid": "01005", "name": "C County"})
        interim["acs_5yr"]["data"]["geos"]["0500000US01005"] = {"B01003_001": pop}

    def test_unsampled_county_placed_is_noted(self):
        """The lens carries the unsampled county's people (ratio exactly 1), so the line is only a NOTE."""
        web, interim = dataset()
        self._add_unsampled_county(web, interim, 1000.0)
        web["lenses.json"]["lenses"][0]["values"][0] *= 2  # cells 0/1 now carry 2000 = counties 01001 + 01005
        web["lenses.json"]["lenses"][0]["values"][1] *= 2
        rep = self.run_check(web, interim)
        self.assertTrue(any("hit by no sample" in m and "consistent" in m for m in self.levels(rep, "NOTE")))

    def test_dropped_unsampled_county_warns(self):
        """A county dropped by the builders: the ratio is short by exactly its share, so the check can't vouch for it."""
        web, interim = dataset()
        self._add_unsampled_county(web, interim, 5.0)  # 0.17%: inside CONSERVATION_TOLERANCE, so only this line can tell
        rep = self.run_check(web, interim)
        self.assertTrue(any("density conservation" in m for m in self.levels(rep, "PASS")))
        self.assertTrue(any("hit by no sample" in m for m in self.levels(rep, "WARN")))


if __name__ == "__main__":
    unittest.main()
