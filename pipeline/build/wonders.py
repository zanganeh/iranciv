"""Wonders: national parks, great research universities and a few iconic landmarks, each picked by a published rule.

* national_park: the most-visited National Parks in the lower 48 (NPS recreation visits, latest complete year).
* park_unit: the most-visited other park units, leaving out linear units (parkways, trails, rivers) whose counts are
  mostly through-traffic, and merging the National Mall memorials (which each count the same visitors).
* research_university: the R1 universities with the most R&D spending (NSF HERD), at most two per metro.
* landmark: a short hand-picked list of National Historic Landmarks and NPS sites, marked curated.

Writes web/data/wonders.json (loaded eagerly; a few tens of kB).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..core.geo import Shape
from ..core.store import load_interim, load_interim_payload, write_web
from .military import Locator, _is_num, _load

REQUIRES = ["_world", "nps_boundaries", "nps_visits", "ipeds", "herd", "nhl_landmarks", "census_boundaries",
            "cbsa_delineation", "cities.json"]
PRODUCES = ["wonders.json"]

N_PARKS, N_UNITS, N_UNIVERSITIES, PER_METRO = 15, 5, 20, 2
LINEAR = {"Parkway", "National Scenic Trail", "National Trails System", "National Wild & Scenic River",
          "National River", "National Recreational River", "National Scenic River"}
MALL = {"LINC": "Lincoln Memorial", "VIVE": "Vietnam Veterans Memorial", "WWII": "World War II Memorial",
        "KOWA": "Korean War Veterans Memorial", "MLKM": "Martin Luther King, Jr. Memorial",
        "FDRM": "Franklin Delano Roosevelt Memorial", "JEFM": "Thomas Jefferson Memorial",
        "WAMO": "Washington Monument"}

# Curated landmarks, in no order of importance: (source, key, display name). At most one per metro, spread across
# regions and themes (founding, frontier, Native nations, engineering, space, culture).
LANDMARKS = [
    ("nps", "STLI", "Statue of Liberty"),
    ("nps", "INDE", "Independence Hall"),
    ("nps", "MORU", "Mount Rushmore"),
    ("nps", "WRBR", "Wright Brothers National Memorial"),
    ("nps", "GOSP", "Golden Spike"),  # not Hoover Dam: Lake Mead NRA (a park-unit wonder) already holds it
    ("nhl", "66000808", "The Alamo"),
    ("nhl", "66000899", "Cahokia Mounds"),
    ("nhl", "66000496", "Taos Pueblo"),
    ("nhl", "84003872", "Cape Canaveral launch complexes"),
    ("nhl", "66000377", "French Quarter (Vieux Carré)"),
    ("nhl", "66000826", "Monticello"),
    ("nhl", "87002652", "Wrigley Field"),
]

# NHL designation years the NHL GIS layer leaves blank, filled by hand: key → (year, note shown with it).
DESIGNATED_FILL = {
    "87002652": (2020, "Designation year filled in by this project: the NHL GIS layer leaves it blank; Wrigley Field "
                       "was designated a National Historic Landmark in 2020."),
}

# What a park unit is, for its art and label: National Parks and other natural units are 'natural'; recreation areas
# 'recreation'; seashores and lakeshores 'coastal'; memorials, historic sites and city monuments 'monument'.
UNIT_KIND = {"National Recreation Area": "recreation", "National Seashore": "coastal", "National Lakeshore": "coastal",
             "National Memorial": "monument", "National Historical Park": "monument", "National Historic Site": "monument",
             "National Battlefield": "monument", "National Military Park": "monument"}
KIND_OVERRIDE = {"JEFF": "monument", "MALL": "monument"}  # the Gateway Arch is a National Park, but a monument
PARK_KINDS = ["natural", "recreation", "coastal", "monument"]

# Where the publisher's state is not the one a reader expects: (state, county GEOID, why).
PLACE_OVERRIDE = {
    "nps-STLI": ("NY", "36061", "Liberty Island is an exclave of New York County inside New Jersey waters"),
}


def park_kind(code: str, unit_type: str | None) -> str:
    return KIND_OVERRIDE.get(code) or UNIT_KIND.get(unit_type or "", "natural")


# Short labels for the map (everything else: the IPEDS name without its campus suffix).
SHORT = {
    "110662": "UCLA", "110699": "UCSF", "110635": "UC Berkeley", "162928": "Johns Hopkins", "166683": "MIT", "139755": "Georgia Tech", "199120": "UNC Chapel Hill",
    "179867": "WashU", "193900": "NYU", "190150": "Columbia", "215062": "Penn", "170976": "Michigan",
    "240444": "UW–Madison", "236948": "Univ. of Washington", "204796": "Ohio State", "163286": "UMD College Park",
    "228723": "Texas A&M", "221999": "Vanderbilt", "215293": "Pitt", "174066": "Minnesota", "228778": "UT Austin",
    "214777": "Penn State", "123961": "USC", "134130": "Florida", "139658": "Emory", "147767": "Northwestern",
    "243744": "Stanford", "130794": "Yale", "190415": "Cornell", "198419": "Duke", "166027": "Harvard",
}
UC = "University of California-"


def _short_univ(uid: str, name: str) -> str:
    if uid in SHORT:
        return SHORT[uid]
    name = name.replace("-Main Campus", "")
    if name.startswith(UC):
        return "UC " + name[len(UC):]
    return name.replace("-", " ")


def _park_short(u: dict) -> str:
    return u.get("park") or u["name"]


def _rules(visits_year: int, herd_year: int, ipeds_year: int) -> list[dict]:
    return [
        {"id": "national_park", "kind": "natural", "kinds": PARK_KINDS, "year": visits_year,
         "text": f"The {N_PARKS} lower-48 National Parks with the most recreation visits in {visits_year} (NPS Visitor "
                 "Use Statistics). Ranked only against other National Parks, because each unit type counts visits "
                 "differently. Each park is drawn by its NPS unit type, so the Gateway Arch counts as a monument."},
        {"id": "park_unit", "kind": "natural", "kinds": PARK_KINDS, "year": visits_year,
         "text": f"The {N_UNITS} most-visited other lower-48 park units in {visits_year} (recreation areas, seashores, "
                 "monuments, memorials ...). Parkways, trails and rivers are left out because their counts are mostly "
                 "through-traffic. The National Mall memorials count the same visitors at each memorial, so they are "
                 "merged into one 'National Mall' wonder ranked by its most-visited memorial, never by their sum. "
                 "Each is drawn by its NPS unit type: recreation areas, seashores and lakeshores, and monuments and "
                 "memorials are not called natural wonders."},
        {"id": "research_university", "kind": "university", "year": herd_year,
         "text": f"The {N_UNIVERSITIES} universities with the most research and development spending in fiscal "
                 f"{herd_year} (NSF HERD, all sources) among those with the R1 Carnegie research designation (IPEDS "
                 f"{ipeds_year}), at most {PER_METRO} per metro. R&D totals include medical schools; a few universities "
                 "report together with their medical centre."},
        {"id": "landmark", "kind": "landmark", "curated": True,
         "text": f"Hand-picked by this project, not ranked by any data: {len(LANDMARKS)} widely recognised National "
                 "Historic Landmarks and NPS sites, at most one per metro, spread across regions and themes. Listed "
                 "alphabetically; positions from the NPS National Register GIS layer or the NPS unit centre point. No "
                 "landmark repeats a site that a park rule already picked."},
    ]


JITTER_KM = (1, 2, 4, 8)


def _county_in(loc: Locator, x, y, cell, state):
    """The county at x/y; when the publisher files the site in another state (a point on a border river, e.g. the
    Gateway Arch on the Mississippi, or a dam on a state line, against 1:20M county outlines), the nearest county of
    the filed state within a few km instead. A multi-state unit filed under one state (Gateway NRA: NJ, centre in
    NY) keeps the county at its point."""
    county = loc.county_at(x, y, cell)
    if not state or (county and loc.abbr_by_fips.get(county[:2]) == state):
        return county
    for r in JITTER_KM:
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, r), (r, -r), (-r, -r)):
            c = loc.county_at(x + dx, y + dy)
            if c and loc.abbr_by_fips.get(c[:2]) == state:
                return c
    return county


def _place(loc: Locator, lon, lat, rings=2, filed: str | None = None, county: str | None = None):
    """Map position of a site. `filed`: the state the publisher lists (postal abbr); `county`: a GEOID the
    publisher gives (IPEDS), used when it is on the map."""
    x, y, cell = loc.place(lon, lat, rings)
    if cell < 0:
        return None
    filed = filed if filed in loc.mapped else None
    if not (county and county in loc.county_set):
        county = _county_in(loc, x, y, cell, filed)
    if county and filed and loc.abbr_by_fips.get(county[:2]) != filed:
        filed = None  # the point is clearly in another state: report that one
    state = loc.state_of(county, cell, filed)
    if state not in loc.mapped:
        return None
    xs, ys = loc.settle(x, y, cell)
    return {"x": xs, "y": ys, "cell": cell, "county": county, "cbsa": loc.cbsa_of.get(county) if county else None,
            "state": state}


def _park_cells(loc: Locator, unit: dict):
    if not unit.get("rings"):
        return None
    shape = Shape([[loc.proj(lon, lat) for lon, lat in r] for r in unit["rings"]])
    return loc.covered_cells([shape]) or None


def parks(loc: Locator, units: dict, visits: dict, year: int):
    nps, other, mall = [], [], []
    for code, v in visits.items():
        u = units.get(code)
        if not u or v["visits"] <= 0:
            continue
        if code in MALL:
            mall.append((v["visits"], code, u))
            continue
        if u["type"] in LINEAR:
            continue
        (nps if u["type"] == "National Park" else other).append((v["visits"], code, u))
    out = []
    nps.sort(key=lambda t: -t[0])
    rank = 0
    for vis, code, u in nps:
        pos = _place(loc, u["lon"], u["lat"], filed=u.get("state"))
        if not pos:
            continue
        rank += 1
        out.append({"id": f"nps-{code}", "name": u["name"], "short": _park_short(u), "kind": park_kind(code, u["type"]),
                    "rule": "national_park", "rank": rank,
                    "metrics": {"visits": vis, "year": year, "area_km2": u["area_km2"], "unit_type": u["type"]},
                    **pos, "cells": _park_cells(loc, u), "curated": False})
        if rank == N_PARKS:
            break
    if mall:
        mall.sort(key=lambda t: -t[0])
        lon = sum(u["lon"] for _, _, u in mall) / len(mall)
        lat = sum(u["lat"] for _, _, u in mall) / len(mall)
        top_vis, top_code, _ = mall[0]
        other.append((top_vis, "MALL", {"name": "National Mall and Memorial Parks", "park": "National Mall",
                                        "type": "National Memorial", "lon": lon, "lat": lat, "area_km2": None, "state": "DC",
                                        "rings": None, "_members": [(c, v) for v, c, _ in mall]}))
    other.sort(key=lambda t: -t[0])
    rank = 0
    for vis, code, u in other:
        pos = _place(loc, u["lon"], u["lat"], filed=u.get("state"))
        if not pos:
            continue
        rank += 1
        metrics = {"visits": vis, "year": year, "area_km2": u["area_km2"], "unit_type": u["type"]}
        if code == "MALL":
            metrics.update(visits_of=MALL[u["_members"][0][0]],
                           memorials={c: v for c, v in u["_members"]},
                           note="Ranked by its most-visited memorial; each memorial counts the same visitors, "
                                "so the counts are not added up.")
        out.append({"id": f"nps-{code}", "name": u["name"], "short": _park_short(u), "kind": park_kind(code, u["type"]),
                    "rule": "park_unit", "rank": rank, "metrics": metrics, **pos,
                    "cells": _park_cells(loc, u), "curated": False})
        if rank == N_UNITS:
            break
    return out


def universities(loc: Locator, herd: dict, ipeds: dict, ipeds_year: int):
    inst = ipeds["institutions"]
    out, per_metro, skipped = [], Counter(), []
    rank = 0
    for h in herd["institutions"]:  # sorted by R&D, largest first
        uid = h.get("unitid")
        i = inst.get(uid) if uid else None
        if not i or i["research"] != 1 or i["lat"] is None or not h.get("rd_usd"):
            continue
        pos = _place(loc, i["lon"], i["lat"], filed=i["state"], county=i["county"])
        if not pos:
            continue
        metro = pos["cbsa"] or i["cbsa"]
        if metro and per_metro[metro] >= PER_METRO:
            skipped.append(i["name"])
            continue
        per_metro[metro] += bool(metro)
        rank += 1
        metrics = {"rd_usd": round(h["rd_usd"]), "federal_usd": round(h["federal_usd"]) if h.get("federal_usd") else None,
                   "year": herd["year"], "enrollment": i["enrollment"], "enrollment_year": ipeds.get("enrollment_year"),
                   "r1": True, "med_school": bool(h.get("med_school"))}
        if h.get("unitid_matched_by_hand") and h["name"] != i["name"]:
            metrics["reported_as"] = h["name"]  # a combined report matched to its lead campus
        out.append({"id": f"univ-{uid}", "name": i["name"].replace("-Main Campus", ""), "short": _short_univ(uid, i["name"]),
                    "kind": "university", "rule": "research_university", "rank": rank, "metrics": metrics, **pos,
                    "cells": None, "curated": False})
        if rank == N_UNIVERSITIES:
            break
    return out, skipped


def landmarks(loc: Locator, units: dict, visits: dict, year: int, nhl: dict, taken: set):
    picked, missing = [], []
    for src, key, name in LANDMARKS:
        wid = f"nps-{key}" if src == "nps" else f"nhl-{key}"
        if wid in taken:
            continue
        if src == "nps":
            u = units.get(key)
            pos = u and _place(loc, u["lon"], u["lat"], filed=u.get("state"))
            metrics = {"visits": visits.get(key, {}).get("visits"), "year": year, "unit_type": u and u["type"]}
            full = u and u["name"]
        else:
            l = nhl.get(key)
            pos = l and _place(loc, l["lon"], l["lat"], filed=loc.state_names.get(l.get("state") or ""))
            metrics = {"designated": int(l["designated"]) if l and l.get("designated") else None,
                       "nhl_ref": key}
            if metrics["designated"] is None and key in DESIGNATED_FILL:
                metrics["designated"], metrics["note"] = DESIGNATED_FILL[key]
            full = l and l["name"]
        if not pos:
            missing.append(name)
            continue
        if wid in PLACE_OVERRIDE:
            st, county, why = PLACE_OVERRIDE[wid]
            pos = dict(pos, state=st, county=county, cbsa=loc.cbsa_of.get(county), state_note=why)
        picked.append({"id": wid, "name": name, "short": name.split(" (")[0], "kind": "landmark", "rule": "landmark",
                       "rank": None, "metrics": metrics, **pos, "cells": None, "curated": True,
                       "listed_as": full})
    picked.sort(key=lambda w: w["name"])
    for k, w in enumerate(picked, 1):
        w["rank"] = k
    return picked, missing


def build() -> None:
    world = load_interim("_world")
    loc = Locator(world)
    nps_p, vis_p, ipeds_p, herd_p, nhl_p = (load_interim_payload(n) for n in
                                            ("nps_boundaries", "nps_visits", "ipeds", "herd", "nhl_landmarks"))
    units = {u["code"]: u for u in nps_p["data"]["units"]}
    visits, year = vis_p["data"]["units"], vis_p["data"]["year"]
    ipeds, herd = ipeds_p["data"], herd_p["data"]

    w = parks(loc, units, visits, year)
    unis, skipped = universities(loc, herd, ipeds, ipeds["year"])
    w += unis
    lm, missing = landmarks(loc, units, visits, year, nhl_p["data"]["landmarks"], {x["id"] for x in w})
    w += lm
    doc = {
        "version": 1,
        "vintage": {"parks": vis_p.get("vintage"), "boundaries": nps_p.get("vintage"),
                    "universities": f"{herd_p.get('vintage')}; {ipeds_p.get('vintage')}",
                    "landmarks": nhl_p.get("vintage")},
        "sources": ["nps_boundaries", "nps_visits", "ipeds", "herd", "nhl_landmarks"],
        "rules": _rules(year, herd["year"], ipeds["year"]),
        "national": {"park_visits": vis_p["data"]["total"], "park_visits_year": year,
                     "r1_universities": sum(1 for i in ipeds["institutions"].values() if i["research"] == 1)},
        "wonders": w,
    }
    write_web("wonders.json", doc)
    kinds = Counter(x["rule"] for x in w)
    print(f"    {len(w)} wonders: {dict(kinds)}; universities skipped by the per-metro cap: {skipped}"
          + (f"; landmarks not found: {missing}" if missing else ""))


# --- check --------------------------------------------------------------------------------

KINDS = {"natural", "recreation", "coastal", "monument", "university", "landmark"}


def check(web_dir) -> list[tuple[str, str]]:
    path = Path(web_dir) / "wonders.json"
    if not path.exists():
        return [("NOTE", "wonders.json not built (optional)")]
    d = json.loads(path.read_text())
    world = _load(web_dir, "world.json") or {}
    cities = _load(web_dir, "cities.json") or []
    g = world.get("grid") or {}
    n_cells = (g.get("cols") or 0) * (g.get("rows") or 0)
    owner = world.get("owner") or []
    counties = {c["geoid"] for c in world.get("counties") or []}
    city_ids = {c["id"] for c in cities}

    def cell_ok(c):
        return isinstance(c, int) and not isinstance(c, bool) and (not n_cells or 0 <= c < n_cells) and \
            (not owner or (c < len(owner) and owner[c] >= 0))

    bad = []
    if d.get("version") != 1:
        bad.append("version must be 1")
    if not isinstance(d.get("sources"), list) or not d.get("sources"):
        bad.append("sources must be a non-empty list")
    rules = {r.get("id"): r for r in d.get("rules") or []}
    if not rules or not all(isinstance(r.get("text"), str) and r.get("kind") in KINDS
                            and set(r.get("kinds") or [r.get("kind")]) <= KINDS for r in rules.values()):
        bad.append("rules must each have an id, a kind (and optional kinds) and text")
    ids, ranks = set(), Counter()
    for x in d.get("wonders") or []:
        tag = f"wonder {x.get('id')!r}"
        if not isinstance(x.get("id"), str) or x["id"] in ids:
            bad.append(f"{tag}: missing or duplicate id")
        ids.add(x.get("id"))
        r = rules.get(x.get("rule"))
        if r is None or x.get("kind") not in (r.get("kinds") or [r.get("kind")]):
            bad.append(f"{tag}: unknown rule or kind not allowed by its rule")
        if not isinstance(x.get("rank"), int) or x["rank"] < 1:
            bad.append(f"{tag}: rank must be an int >= 1")
        ranks[(x.get("rule"), x.get("rank"))] += 1
        if not isinstance(x.get("name"), str) or not isinstance(x.get("short"), str):
            bad.append(f"{tag}: name/short missing")
        if not isinstance(x.get("metrics"), dict):
            bad.append(f"{tag}: metrics must be an object")
        if not cell_ok(x.get("cell")) or not _is_num(x.get("x")) or not _is_num(x.get("y")):
            bad.append(f"{tag}: bad cell/x/y")
        if x.get("cells") is not None and (not x["cells"] or not all(cell_ok(c) for c in x["cells"])):
            bad.append(f"{tag}: cells must be null or a non-empty list of US land cells")
        if x.get("county") is not None and counties and x["county"] not in counties:
            bad.append(f"{tag}: county not in world.json")
        if x.get("cbsa") is not None and city_ids and x["cbsa"] not in city_ids:
            bad.append(f"{tag}: cbsa not in cities.json")
        if not isinstance(x.get("curated"), bool) or x.get("curated") != bool(rules.get(x.get("rule"), {}).get("curated")):
            bad.append(f"{tag}: curated must match its rule")
        m = x.get("metrics") or {}
        for k in ("visits", "rd_usd", "federal_usd", "enrollment"):
            if m.get(k) is not None and not (_is_num(m[k]) and m[k] > 0):
                bad.append(f"{tag}: metrics.{k} must be > 0 or null")
    dup = [k for k, v in ranks.items() if v > 1]
    if dup:
        bad.append(f"duplicate ranks within a rule: {dup[:3]}")
    out = [("FAIL", f"wonders.json: {m}") for m in bad[:12]]
    if len(bad) > 12:
        out.append(("FAIL", f"wonders.json: … and {len(bad) - 12} more problems"))
    size = path.stat().st_size
    out.append(("WARN" if size > 300_000 else "PASS", f"wonders.json {size / 1e3:.0f} kB (eager budget ~300 kB)"))
    n = len(d.get("wonders") or [])
    by_rule = Counter(x.get("rule") for x in d.get("wonders") or [])
    if not bad:
        out.append(("PASS", f"wonders.json: {n} wonders ({', '.join(f'{v} {k}' for k, v in by_rule.items())})"))
    if not 30 <= n <= 60:
        out.append(("WARN", f"wonders.json: {n} wonders (expected about 30-60)"))
    return out
