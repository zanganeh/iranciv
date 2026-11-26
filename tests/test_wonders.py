"""Wonders: the selection rules (parks within unit types, the National Mall merge, R1 universities with a
per-metro cap, curated landmarks) and wonders.json's check()."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.build import wonders
from pipeline.sources import nps

from test_check import dataset


def unit(code, type_, state="UT", rings=None, park=None):
    return {"code": code, "name": f"{code} {type_}", "park": park or code, "type": type_, "state": state,
            "lon": -100.0, "lat": 40.0, "area_km2": 10.0, "rings": rings}


def fake_place(loc, lon, lat, rings=2, filed=None, county=None):
    if filed == "AK":
        return None  # outside the lower 48
    return {"x": 1.0, "y": 1.0, "cell": 0, "county": "01001", "cbsa": county or "10000", "state": filed or "AL"}


class ParkRulesTest(unittest.TestCase):
    def test_parks_rank_within_type_merge_the_mall_and_skip_linear_units(self):
        units = {c: unit(c, t, state=s) for c, t, s in [
            ("BLRI", "Parkway", "NC"), ("GOGA", "National Recreation Area", "CA"), ("ZION", "National Park", "UT"),
            ("DENA", "National Park", "AK"), ("YELL", "National Park", "WY"), ("LINC", "National Memorial", "DC"),
            ("VIVE", "National Memorial", "DC"), ("APPA", "National Scenic Trail", "CT")]}
        visits = {"BLRI": 16e6, "GOGA": 15e6, "ZION": 5e6, "DENA": 9e6, "YELL": 4e6, "LINC": 7e6, "VIVE": 6e6,
                  "APPA": 8e6}
        visits = {k: {"visits": int(v)} for k, v in visits.items()}
        with mock.patch.object(wonders, "_place", fake_place), mock.patch.object(wonders, "_park_cells",
                                                                               lambda loc, u: None):
            out = wonders.parks(None, units, visits, 2025)
        by_rule = {}
        for w in out:
            by_rule.setdefault(w["rule"], []).append(w)
        self.assertEqual([w["id"] for w in by_rule["national_park"]], ["nps-ZION", "nps-YELL"])  # DENA is in AK
        self.assertEqual([w["rank"] for w in by_rule["national_park"]], [1, 2])
        self.assertEqual([w["id"] for w in by_rule["park_unit"]], ["nps-GOGA", "nps-MALL"])
        mall = by_rule["park_unit"][1]
        self.assertEqual(mall["metrics"]["visits"], 7_000_000)  # the busiest memorial, never the 13M sum
        self.assertEqual(set(mall["metrics"]["memorials"]), {"LINC", "VIVE"})
        self.assertFalse(any(w["id"] in ("nps-BLRI", "nps-APPA") for w in out))
        kinds = {w["id"]: w["kind"] for w in out}
        self.assertEqual(kinds, {"nps-ZION": "natural", "nps-YELL": "natural", "nps-GOGA": "recreation",
                                 "nps-MALL": "monument"})  # the Mall and NRAs are not natural wonders

    def test_park_kinds(self):
        self.assertEqual(wonders.park_kind("JEFF", "National Park"), "monument")  # the Gateway Arch
        self.assertEqual(wonders.park_kind("GUIS", "National Seashore"), "coastal")
        self.assertEqual(wonders.park_kind("LAKE", "National Recreation Area"), "recreation")
        self.assertEqual(wonders.park_kind("GRCA", "National Park"), "natural")
        self.assertEqual(wonders.park_kind("MUWO", "National Monument"), "natural")  # many monuments are landscapes

    def test_unit_type_normalisation(self):
        self.assertEqual(nps.unit_type("National Parks"), "National Park")
        self.assertEqual(nps.unit_type("Parkways"), "Parkway")
        self.assertEqual(nps.unit_type("National Recreation Areas"), "National Recreation Area")
        self.assertEqual(nps.unit_type("National Park"), "National Park")


class UniversityRuleTest(unittest.TestCase):
    def test_r1_only_cap_per_metro_and_missing_ids(self):
        inst = {str(u): {"name": f"U{u}", "research": r, "lat": 40.0, "lon": -100.0, "state": "AL", "county": None,
                         "cbsa": m, "enrollment": 1000}
                for u, r, m in [(1, 1, "A"), (2, 1, "A"), (3, 1, "A"), (4, 2, "B"), (5, 1, "B"), (6, 1, None)]}
        herd = {"year": 2025, "institutions": [
            {"name": "No id", "unitid": None, "rd_usd": 9e9},
            {"name": "U1", "unitid": "1", "rd_usd": 5e9, "federal_usd": 2e9},
            {"name": "U2", "unitid": "2", "rd_usd": 4e9},
            {"name": "U3", "unitid": "3", "rd_usd": 3e9},  # third in metro A: capped
            {"name": "U4", "unitid": "4", "rd_usd": 2e9},  # R2: not eligible
            {"name": "U5", "unitid": "5", "rd_usd": 1e9},
            {"name": "U6", "unitid": "6", "rd_usd": 0.5e9}]}
        def place(loc, lon, lat, rings=2, filed=None, county=None):  # no metro on the map: IPEDS CBSA is used
            return {"x": 1.0, "y": 1.0, "cell": 0, "county": None, "cbsa": None, "state": "AL"}

        with mock.patch.object(wonders, "_place", place):
            out, skipped = wonders.universities(None, herd, {"institutions": inst, "enrollment_year": 2024}, 2024)
        self.assertEqual([w["id"] for w in out], ["univ-1", "univ-2", "univ-5", "univ-6"])
        self.assertEqual(skipped, ["U3"])
        self.assertEqual([w["rank"] for w in out], [1, 2, 3, 4])
        self.assertEqual(out[0]["metrics"]["rd_usd"], 5_000_000_000)
        self.assertIsNone(out[1]["metrics"]["federal_usd"])  # unknown stays null

    def test_landmarks_are_curated_alphabetical_and_skip_taken(self):
        nhl = {ref: {"name": name, "lon": -100.0, "lat": 40.0, "state": "TEXAS", "designated": "1966"}
               for _, ref, name in wonders.LANDMARKS}
        units = {key: unit(key, "National Monument", state="NY") for src, key, _ in wonders.LANDMARKS if src == "nps"}
        loc = mock.Mock(state_names={"TEXAS": "TX"})
        with mock.patch.object(wonders, "_place", fake_place):
            out, missing = wonders.landmarks(loc, units, {}, 2025, nhl, {"nps-STLI"})
        self.assertEqual(missing, [])
        self.assertEqual(len(out), len(wonders.LANDMARKS) - 1)
        self.assertTrue(all(w["curated"] and w["rule"] == "landmark" for w in out))
        self.assertEqual([w["name"] for w in out], sorted(w["name"] for w in out))
        self.assertIsNone(next(w for w in out if w["id"].startswith("nps-"))["metrics"]["visits"])  # null, not 0

    def test_statue_of_liberty_is_in_new_york(self):
        units = {"STLI": unit("STLI", "National Monument", state="NJ")}
        loc = mock.Mock(state_names={}, cbsa_of={"36061": "35620"})
        with mock.patch.object(wonders, "_place", fake_place), mock.patch.object(wonders, "LANDMARKS",
                                                                               [("nps", "STLI", "Statue of Liberty")]):
            out, _ = wonders.landmarks(loc, units, {}, 2025, {}, set())
        self.assertEqual((out[0]["state"], out[0]["county"], out[0]["cbsa"]), ("NY", "36061", "35620"))
        self.assertTrue(out[0]["state_note"])


def good_doc():
    return {
        "version": 1, "sources": ["nps_visits"],
        "rules": [{"id": "national_park", "kind": "natural", "text": "t"},
                  {"id": "landmark", "kind": "landmark", "curated": True, "text": "t"}],
        "wonders": [{"id": f"nps-P{k}", "name": "P", "short": "P", "kind": "natural", "rule": "national_park",
                     "rank": k, "metrics": {"visits": 10, "year": 2025}, "x": 1.0, "y": 1.0, "cell": 0,
                     "cells": [0, 1] if k == 1 else None, "county": "01001", "cbsa": "10000", "state": "AL",
                     "curated": False} for k in range(1, 31)]
                   + [{"id": "nhl-1", "name": "L", "short": "L", "kind": "landmark", "rule": "landmark", "rank": 1,
                       "metrics": {}, "x": 1.0, "y": 1.0, "cell": 3, "cells": None, "county": None, "cbsa": None,
                       "state": "AL", "curated": True}],
    }


class WondersCheckTest(unittest.TestCase):
    def run_check(self, doc):
        web, _ = dataset()
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("world.json", "cities.json"):
                (Path(tmp) / name).write_text(json.dumps(web[name]))
            if doc is not None:
                (Path(tmp) / "wonders.json").write_text(json.dumps(doc))
            return wonders.check(Path(tmp))

    def test_missing_file_is_a_note(self):
        self.assertEqual([lv for lv, _ in self.run_check(None)], ["NOTE"])

    def test_valid_file_passes(self):
        lines = self.run_check(good_doc())
        self.assertFalse([m for lv, m in lines if lv in ("FAIL", "WARN")], lines)
        doc = good_doc()
        doc["rules"][0]["kinds"] = wonders.PARK_KINDS
        doc["wonders"][0]["kind"] = "monument"
        lines = self.run_check(doc)
        self.assertFalse([m for lv, m in lines if lv in ("FAIL", "WARN")], lines)

    def test_violations_fail(self):
        cases = {
            "ocean cell": lambda d: d["wonders"][0].update(cell=2),
            "rule": lambda d: d["wonders"][0].update(rule="nope"),
            "kind": lambda d: d["wonders"][0].update(kind="landmark"),
            "kind not in kinds": lambda d: d["wonders"][0].update(kind="monument"),
            "unknown kinds": lambda d: d["rules"][0].update(kinds=["natural", "castle"]),
            "rank": lambda d: d["wonders"][1].update(rank=1),
            "curated": lambda d: d["wonders"][-1].update(curated=False),
            "visits zero": lambda d: d["wonders"][0]["metrics"].update(visits=0),
            "cells": lambda d: d["wonders"][0].update(cells=[5]),
            "cbsa": lambda d: d["wonders"][0].update(cbsa="99999"),
        }
        for what, change in cases.items():
            doc = copy.deepcopy(good_doc())
            change(doc)
            with self.subTest(what=what):
                self.assertTrue([m for lv, m in self.run_check(doc) if lv == "FAIL"], what)


if __name__ == "__main__":
    unittest.main()
