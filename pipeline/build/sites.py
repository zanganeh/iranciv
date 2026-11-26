"""Resources (power plants, mines, oil and gas fields) and wonders (World Heritage, national parks, universities).

Every site is placed on the Iranian land hex at its point, or the nearest one within 2 hexes (offshore fields and
island sites). Nuclear plants are left out of this map. Mines are the Wikidata mines whose commodity is known; the
commodity comes from a short hand-written table (MINE_COMMODITY), marked as such in each item's detail.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, write_web
from .economy import EXCLUDED_FUELS, land_cell, whs_properties

REQUIRES = ["_world", "iran_boundaries", "wikidata_sites", "wri_power_plants", "counties.json", "cities.json"]
PRODUCES = ["resources.json", "wonders.json"]


def _t(id, name, category, emoji, icon, color, unit, description, source):
    return {"id": id, "name": name, "category": category, "emoji": emoji, "icon": icon, "color": color, "unit": unit,
            "description": description, "source": source}


PLANT_TYPES = {
    "Gas": _t("gas_plant", "Gas power plant", "energy", "⚡", "r-gas_plant", "#f08a24", "MW",
              "Every gas-fired power station in the WRI Global Power Plant Database (2021).", "wri_power_plants"),
    "Oil": _t("oil_plant", "Oil-fired power plant", "energy", "🏭", "r-coal_plant", "#6b4f3a", "MW",
              "Every oil-fired power station in the WRI Global Power Plant Database (2021).", "wri_power_plants"),
    "Hydro": _t("hydro", "Hydroelectric dam", "energy", "💧", "r-hydro", "#3fa7d6", "MW",
                "Every hydroelectric station in the WRI Global Power Plant Database (2021).", "wri_power_plants"),
    "Wind": _t("wind", "Wind farm", "energy", "🌬️", "r-wind", "#9fd8cb", "MW",
               "Every wind farm in the WRI Global Power Plant Database (2021).", "wri_power_plants"),
    "Solar": _t("solar", "Solar farm", "energy", "☀️", "r-solar", "#ffc93c", "MW",
                "Every solar farm in the WRI Global Power Plant Database (2021).", "wri_power_plants"),
}
FIELD_TYPES = [
    _t("oil", "Oil field", "energy", "🛢️", "r-oil", "#3a2f2a", "field",
       "Oil fields with a Wikidata item and coordinates: the best-known fields, not a full inventory.", "wikidata_sites"),
    _t("gas", "Gas field", "energy", "🔥", "r-gas", "#8fb3c9", "field",
       "Natural gas fields with a Wikidata item and coordinates, including South Pars, the Iranian side of the "
       "world's largest gas field. Not a full inventory.", "wikidata_sites"),
]
MINE_TYPES = {
    "iron": _t("iron", "Iron ore mine", "mineral", "⛓️", "r-iron", "#8a4b2a", "mine",
               "Iron ore mines with a Wikidata item and coordinates.", "wikidata_sites"),
    "copper": _t("copper", "Copper mine", "mineral", "🥉", "r-copper", "#b87333", "mine",
                 "Copper mines with a Wikidata item and coordinates.", "wikidata_sites"),
    "gold": _t("gold", "Gold mine", "mineral", "🥇", "r-gold", "#d4af37", "mine",
               "Gold mines with a Wikidata item and coordinates.", "wikidata_sites"),
    "lead_zinc": _t("lead_zinc", "Lead-zinc mine", "mineral", "🪨", "r-lead_zinc", "#7d8a99", "mine",
                    "Lead and zinc mines with a Wikidata item and coordinates.", "wikidata_sites"),
}
# Hand-written: commodity of each Wikidata mine. Mines not listed here are left out.
MINE_COMMODITY = {
    "Chadormalu": "iron", "Gol Gohar iron ore mine": "iron",
    "Sarcheshmeh": "copper", "Miduk Copper Mine": "copper", "Sungun copper mine": "copper",
    "Talmessi mine": "copper",
    "Zareh Shuran Mine": "gold",
    "Mehdiabad mine": "lead_zinc", "Nakhlak Mine": "lead_zinc", "Koushk deposit": "lead_zinc",
}


class Placer:
    def __init__(self, world):
        self.proj, self.grid, self.owner = MapProjection(**world["projection"]), HexGrid(**world["grid"]), world["owner"]
        self.county, self.geoids = world["county"], world["counties"]
        abbr = {s["fips"]: s["abbr"] for s in load_interim("iran_boundaries")["states"]}
        self.abbrs = [abbr[f] for f in world["states"]]
        counties = json.loads((WEB_DATA / "counties.json").read_text())
        gi, ci = counties["columns"].index("geoid"), counties["columns"].index("cbsa")
        self.cbsa_of = {r[gi]: r[ci] for r in counties["rows"] if r[ci]}

    def __call__(self, lon, lat, rings=2):
        if lon is None or lat is None:
            return None
        x, y = self.proj(lon, lat)
        cell = land_cell(self.grid, self.owner, x, y, max_rings=rings)
        if cell < 0:
            return None
        k = self.county[cell]
        geoid = self.geoids[k] if k is not None and k >= 0 else None
        return {"x": round(x, 1), "y": round(y, 1), "cell": cell, "county": geoid,
                "cbsa": self.cbsa_of.get(geoid), "state": self.abbrs[self.owner[cell]]}


def resources(place, sites, plants):
    items = []
    for p in sorted(plants, key=lambda p: -(p["mw"] or 0)):
        t = PLANT_TYPES.get(p["fuel"])
        if p["fuel"] in EXCLUDED_FUELS or not t:
            continue
        at = place(p["lon"], p["lat"])
        if at:
            yr = f", online {int(p['year'])}" if p["year"] else ""
            items.append({"type": t["id"], "name": p["name"], "value": round(p["mw"]) if p["mw"] else None,
                          "detail": f"{p['fuel']}{yr}" + (f", {p['owner']}" if p["owner"] else ""), **at})
    for kind in ("oil", "gas"):
        for f in sites[kind]:
            at = place(f["lon"], f["lat"], rings=4)  # offshore fields go to the nearest coast
            if at:
                items.append({"type": kind, "name": f["name"], "value": None,
                              "detail": f"Wikidata {f['qid']}", **at})
    for m in sites["mines"]:
        com = MINE_COMMODITY.get(m["name"])
        at = place(m["lon"], m["lat"]) if com else None
        if at:
            items.append({"type": com, "name": m["name"], "value": None,
                          "detail": f"Wikidata {m['qid']}; commodity from this project's table", **at})
    types = list(PLANT_TYPES.values()) + FIELD_TYPES + list(MINE_TYPES.values())
    present = {i["type"] for i in items}
    types = [t for t in types if t["id"] in present]

    def blank():
        return {"count": 0, "value": None, "unit": None}

    national, states = {}, defaultdict(dict)
    for t in types:
        its = [i for i in items if i["type"] == t["id"]]
        vals = [i["value"] for i in its if i["value"] is not None]
        national[t["id"]] = {"count": len(its), "value": sum(vals) if t["unit"] == "MW" else None, "unit": t["unit"]}
        for i in its:
            s = states[i["state"]].setdefault(t["id"], {"count": 0, "value": 0 if t["unit"] == "MW" else None,
                                                        "unit": t["unit"]})
            s["count"] += 1
            if t["unit"] == "MW" and i["value"]:
                s["value"] += i["value"]
    summary = {"note": "Totals of the mapped sites only. Power plants are the WRI database (2021) without nuclear; "
                       "oil and gas fields and mines are those with Wikidata coordinates, not a full inventory.",
               "national": national, "states": dict(states)}
    return {"types": types, "items": items, "summary": summary}


def wonders(place, sites):
    out = []
    for rank, g in enumerate(sorted(whs_properties(sites["whs"]), key=lambda g: (g[0]["year"] or 9999, g[0]["name"])), 1):
        main = min(g, key=lambda s: int(s["qid"][1:]))  # the oldest item is usually the property itself
        at = place(main["lon"], main["lat"])
        if at:
            out.append({"id": f"whs-{main['qid']}", "name": main["name"], "short": main["name"], "kind": "landmark",
                        "rule": "landmark", "rank": rank, "curated": False,
                        "metrics": {"inscribed": main["year"], "parts": [s["name"] for s in g if s is not main]},
                        "cells": None, **at})
    parks = sorted((p for p in sites["parks"] if p["lon"] is not None), key=lambda p: -(p["area_ha"] or 0))
    for rank, p in enumerate(parks, 1):
        at = place(p["lon"], p["lat"])
        if at:
            out.append({"id": f"np-{p['qid']}", "name": p["name"], "short": p["name"].replace(" National Park", ""),
                        "kind": "natural", "rule": "national_park", "rank": rank, "curated": False,
                        "metrics": {"area_km2": round(p["area_ha"] / 100) if p["area_ha"] else None,
                                    "visits": None, "year": None, "unit_type": "National Park"},
                        "cells": None, **at})
    unis = sorted(sites["universities"], key=lambda u: (-u["sitelinks"], u["name"]))
    for rank, u in enumerate(unis[:20], 1):
        at = place(u["lon"], u["lat"])
        if at:
            out.append({"id": f"uni-{u['qid']}", "name": u["name"], "short": u["name"], "kind": "university",
                        "rule": "research_university", "rank": rank, "curated": False,
                        "metrics": {"wikipedia_languages": u["sitelinks"], "enrollment": u["students"],
                                    "rd_usd": None, "year": None},
                        "cells": None, **at})
    order = {"national_park": 0, "research_university": 1, "landmark": 2}
    out.sort(key=lambda w: (order[w["rule"]], w["rank"]))
    rules = [
        {"id": "national_park", "kind": "natural", "kinds": ["natural"], "year": None,
         "text": "Iran's national parks with a Wikidata item and coordinates, ranked by area."},
        {"id": "research_university", "kind": "university", "year": None,
         "text": "The 20 universities with Wikipedia articles in the most languages (Wikidata sitelinks), a rough "
                 "measure of international prominence."},
        {"id": "landmark", "kind": "landmark", "curated": False,
         "text": "UNESCO World Heritage properties in Iran (Wikidata), ranked by year of inscription. Parts of one "
                 "property inscribed the same year within 40 km are merged; serial properties without coordinates "
                 "(the Persian Garden) and the newest inscription (Alamut) are not placed."},
    ]
    return {"version": 1, "vintage": {"all": "Wikidata, queried 2026-11-26"}, "sources": ["wikidata_sites"],
            "rules": rules, "national": {"world_heritage": sum(1 for w in out if w["rule"] == "landmark"),
                                         "national_parks": len(sites["parks"])},
            "wonders": out}


def build() -> None:
    world = load_interim("_world")
    sites = load_interim("wikidata_sites")
    plants = load_interim("wri_power_plants")["plants"]
    place = Placer(world)
    res = resources(place, sites, plants)
    won = wonders(place, sites)
    write_web("resources.json", res)
    write_web("wonders.json", won)
    # Province energy totals (states.json `energy`), from the mapped MW sites.
    states = json.loads((WEB_DATA / "states.json").read_text())
    mw = [t["id"] for t in res["types"] if t["unit"] == "MW"]
    for st in states:
        its = [i for i in res["items"] if i["state"] == st["abbr"] and i["type"] in mw]
        by = {t: round(sum(i["value"] or 0 for i in its if i["type"] == t)) for t in mw}
        st["energy"] = {"unit": "MW", "total": sum(by.values()), "by_type": by,
                        "sites": {t: sum(1 for i in its if i["type"] == t) for t in mw},
                        "note": "Power plants in the WRI Global Power Plant Database (2021) without nuclear; plants "
                                "built since 2021 are missing."}
    write_web("states.json", states)
    print(f"    {len(res['items'])} resource sites in {len(res['types'])} types; {len(won['wonders'])} wonders")
