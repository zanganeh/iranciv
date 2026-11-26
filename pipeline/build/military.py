"""Military layer: DoD installations as forts, the largest ranges as restricted ground, troops and defense dollars
by state, and the Encampment rule for cities. Writes web/data/military.json (loaded eagerly, so kept small).

* Installations: MIRTA points in the lower 48 (closed and excess sites left out), with component, joint-base flag,
  boundary area and the hexes their boundary covers. MIRTA has no headcount and DoD publishes none per base, so the
  `tier` comes from boundary area and component, plus a short published list of flagship active-duty bases on small
  land (Naval Station Norfolk ...), flagged `curated`. Guard and Reserve sites are never 'major'. Small parcels (under
  1 km²) that share a hex and component with a larger installation are folded into it (`parts`).
* Ranges: the largest boundaries (training and test ranges, the biggest posts), drawn as restricted ground.
* States: DMDC personnel by duty state and service; OLDCC defense spending (contracts, grants, payroll).
* Cities: the Encampment tier from installations in the metro plus ACS armed forces by residence.

Also home to `Locator` (lon/lat → map km, US land cell, county, metro), shared with wonders.py.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..core.geo import MapProjection, Shape, ShapeIndex
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["_world", "mirta", "dmdc_personnel", "oldcc_spending", "acs_5yr", "census_boundaries",
            "cbsa_delineation", "cities.json"]
PRODUCES = ["military.json"]

COMPONENTS = [
    {"id": "army", "name": "Army", "codes": ["usa"]},
    {"id": "navy", "name": "Navy", "codes": ["usn"]},
    {"id": "air_force", "name": "Air Force & Space Force", "codes": ["usaf"]},
    {"id": "marines", "name": "Marine Corps", "codes": ["usmc"]},
    {"id": "army_reserve", "name": "Army Reserve", "codes": ["usar"]},
    {"id": "navy_reserve", "name": "Navy Reserve", "codes": ["usnr"]},
    {"id": "air_force_reserve", "name": "Air Force Reserve", "codes": ["afr"]},
    {"id": "marine_reserve", "name": "Marine Corps Reserve", "codes": ["usmcr"]},
    {"id": "army_guard", "name": "Army National Guard", "codes": ["armyNationalGuard"]},
    {"id": "air_guard", "name": "Air National Guard", "codes": ["airNationalGuard"]},
    {"id": "other", "name": "Other DoD (e.g. Washington Headquarters Services)", "codes": ["whs", "other"]},
]
COMPONENT_IDS = [c["id"] for c in COMPONENTS]
SHORT = {"army": "usa", "navy": "usn", "air_force": "usaf", "marines": "usmc", "army_reserve": "usar",
         "navy_reserve": "usnr", "air_force_reserve": "afr", "marine_reserve": "usmcr", "army_guard": "arng",
         "air_guard": "ang", "other": "dod"}  # id suffixes
ACTIVE_DUTY = {"army", "navy", "air_force", "marines"}
GUARD = {"army_guard", "air_guard"}
NAVAL = {"navy", "marines", "navy_reserve", "marine_reserve"}
STATUSES = ["act", "semi", "care", "clsd", "excs"]
SHOWN_STATUS = {"act", "semi", "care"}  # active, semi-active, caretaker; closed and excess sites are left out

MAJOR_KM2 = 100.0  # ~25,000 acres
BASE_KM2 = 10.0
PARCEL_KM2 = 1.0
TIERS = ["major", "base", "site"]
RESERVE = {"army_reserve", "navy_reserve", "air_force_reserve", "marine_reserve"}

# Flagship active-duty installations that hold far more people than their land suggests (fleet bases, air logistics
# complexes, headquarters posts). Editorial, like the wonder landmarks: these are widely known as among the largest
# US bases by personnel, but DoD publishes no per-base headcount to rank them by. (exact MIRTA site name, component)
FLAGSHIPS = [
    ("Naval Station Norfolk", "navy"), ("Naval Air Station Oceana", "navy"), ("Naval Base San Diego", "navy"),
    ("NAVBASE Coronado", "navy"), ("Naval Base Kitsap Bangor", "navy"), ("NAS Jacksonville FL", "navy"),
    ("NAVSTA Mayport FL", "navy"), ("Naval Submarine Base Kings Bay", "navy"), ("NAS Pensacola FL", "navy"),
    ("Naval Station Great Lakes IL", "navy"), ("Naval Air Station Whidbey Island", "navy"),
    ("Naval Air Station Patuxent River", "navy"), ("Marine Corps Air Station Miramar", "marines"),
    ("Joint Base Langley-Eustis", "air_force"), ("Joint Base Andrews", "air_force"),
    ("Joint Base Charleston", "air_force"), ("Wright-Patterson Air Force Base", "air_force"),
    ("Nellis Air Force Base", "air_force"), ("Hill Air Force Base", "air_force"), ("Tinker Air Force Base", "air_force"),
    ("Robins AFB", "air_force"), ("Travis Air Force Base", "air_force"), ("MacDill Air Force Base", "air_force"),
    ("Keesler AFB", "air_force"), ("Offutt Air Force Base", "air_force"), ("Scott Air Force Base", "air_force"),
    ("Fort Belvoir", "army"), ("Fort Meade", "army"),
]
# One installation filed as several MIRTA sites: (site, component) → the (site, component) it belongs to.
ALIASES = {
    ("barry goldwater range", "marines"): ("the barry m goldwater air force range", "air_force"),
    ("barry m goldwater range", "air_force"): ("the barry m goldwater air force range", "air_force"),
}
TIER_RULE = (f"No per-installation headcount is published, so size comes from the MIRTA boundary and component: "
             f"'major' = an active-duty site (Army, Navy, Air Force incl. Space Force, Marine Corps, or other DoD) with "
             f"a boundary of at least {MAJOR_KM2:.0f} km² (about 25,000 acres), or one of {len(FLAGSHIPS)} flagship "
             f"active-duty bases on small land picked by this project (fleet bases, air logistics complexes, "
             f"headquarters posts such as Naval Station Norfolk; flagged curated); 'base' = any other active-duty site "
             f"of at least {PARCEL_KM2:g} km², or a Guard or Reserve site of at least {BASE_KM2:.0f} km² (Guard and "
             f"Reserve are never 'major'); 'site' = everything else. Parcels under {PARCEL_KM2:g} km² that share a hex "
             f"and component with a larger installation are folded into the nearest one and listed as its parts; the "
             f"Barry M. Goldwater Range's Air Force and Marine Corps halves are one installation.")

# Army posts renamed after MIRTA FY2025 was compiled. MIRTA already has the early-2025 reversions (Fort Bragg,
# Fort Benning); on 10 June 2025 the Army restored seven more names, each now honouring a different soldier.
# Applied to every installation, part and range name; the MIRTA spelling is kept as `name_mirta`.
RENAMES_SOURCE = "https://www.army.mil/article/286223/army_to_change_names_of_seven_installations"
RENAMES_DATE = "2025-06-10"
RENAMES = [  # (MIRTA name as a whole phrase, current official name)
    ("Fort Cavazos", "Fort Hood"), ("Fort Johnson", "Fort Polk"), ("Fort Novosel", "Fort Rucker"),
    ("Fort Eisenhower", "Fort Gordon"), ("Fort Gregg-Adams", "Fort Lee"), ("Fort Walker", "Fort A.P. Hill"),
    ("Fort Barfoot", "Fort Pickett"),
]
_RENAME_RE = [(re.compile(rf"\b{re.escape(a)}\b"), b) for a, b in RENAMES]
RENAME_RULE = (f"Names follow MIRTA FY2025 except seven Army posts whose names the Army restored on 10 June 2025 "
               f"({', '.join(f'{a} is now {b}' for a, b in RENAMES)}; source: {RENAMES_SOURCE}). The MIRTA spelling "
               "is kept alongside the current name.")


def current_name(name: str) -> str:
    """MIRTA site name → current official name (the 2025 Army renames)."""
    for rx, new in _RENAME_RE:
        name = rx.sub(new, name)
    return name


def apply_renames(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        new = current_name(r["name"])
        if new != r["name"]:
            r["name_mirta"], r["name"] = r["name"], new
            n += 1
        if r.get("parts"):
            r["parts"] = [current_name(p) for p in r["parts"]]
    return n


RANGES_N = 30
RANGE_MIN_KM2 = 400.0
RANGE_RULE = (f"The {RANGES_N} largest MIRTA boundaries in the lower 48 (by area, at least {RANGE_MIN_KM2:.0f} km²; "
              "sites with several boundaries are summed), active or semi-active. Their cells are the hexes whose centre "
              "lies inside the boundary; a range that covers no hex centre is shown on the one hex holding its point. "
              "A hex is about 1,600 km², so small ranges are drawn larger than they are. A range's state is its "
              "installation's state (where the installation point lies), so a range that spills over a state line "
              "(Fort Bliss: TX headquarters, mostly New Mexico ground) is listed under the same state as its "
              "installation; the state holding most of its hexes is given too.")

AF_SHARE_MIN, AF_COUNT_MIN, AF_COUNT_SHARE, FORTRESS_SHARE = 0.01, 2000, 0.003, 0.03
TIER_WEIGHT = {"major": 4, "base": 1, "site": 0}
CITY_RULE = ("A metro gets an Encampment when (a) an active non-Guard MIRTA installation of tier 'major' or 'base' "
             "(not a small reserve centre or office) lies in one of its member counties, or one of the restricted "
             "ranges covers one of its hexes, and (b) residents in the armed forces (ACS 5-year B23025_006, by place "
             "of residence, summed over the member counties) are at least "
             f"{AF_SHARE_MIN:.0%} of the population 16 and over, or at least {AF_COUNT_MIN:,} and at least "
             f"{AF_COUNT_SHARE:.1%}. Tier: 'fortress' at {FORTRESS_SHARE:.0%} or more, otherwise 'garrison'. 'naval' "
             "when the metro's qualifying Navy and Marine Corps installations outweigh its Army and Air Force ones, "
             f"counting a 'major' installation as {TIER_WEIGHT['major']} and a 'base' as 1.")

CAVEATS = ("Installation points and boundaries: DoD MIRTA (not every DoD site is listed; Coast Guard sites are not "
           "DoD). No per-base personnel figure is published, so bases are sized by area, not troops, plus a short curated "
           "list of flagship bases on small land. Personnel are by "
           "duty state (DMDC), excluding classified data and most deployed or temporary-duty personnel. Contract "
           "dollars follow the place of performance (OLDCC), not where bases are. Armed forces by county count where "
           "service members live (ACS). Seven Army posts carry the names the Army restored in June 2025 (Fort Hood, "
           "Fort Polk, Fort Rucker, Fort Gordon, Fort Lee, Fort A.P. Hill, Fort Pickett); MIRTA FY2025 still uses "
           "their 2023 names, which the data keeps alongside.")


# --- shared geography ----------------------------------------------------------------


class Locator:
    """lon/lat → (x, y, US land cell) and map km → county GEOID / metro id / state abbr."""

    def __init__(self, world: dict):
        self.world = world
        self.proj = MapProjection(**world["projection"])
        self.grid = HexGrid(**world["grid"])
        self.owner = world["owner"]
        bounds = load_interim("census_boundaries")
        abbr_of = {s["fips"]: s["abbr"] for s in bounds["states"]}
        self.abbrs = [abbr_of.get(f) for f in world["states"]]
        self.mapped = {a for a in self.abbrs if a}
        self.state_names = {s["name"].upper(): s["abbr"] for s in bounds["states"]}
        self.abbr_by_fips = abbr_of
        keep = self.county_set = set(world["counties"])
        self.county_ids, shapes = [], []
        for c in bounds["counties"]:
            if c["geoid"] in keep:
                self.county_ids.append(c["geoid"])
                shapes.append(Shape([[self.proj(lon, lat) for lon, lat in ring] for ring in c["rings"]]))
        self.county_index = ShapeIndex(shapes, 100.0)
        self.counties_by_name = bounds["counties"]
        cities = json.loads((WEB_DATA / "cities.json").read_text())
        self.cities = {c["id"]: c for c in cities}
        self.cbsa_of = {g: code for code, geoids in load_interim("cbsa_delineation")["counties"].items()
                        if code in self.cities for g in geoids}

    def land_cell(self, x: float, y: float, rings: int = 1) -> int:
        """The US land cell at x/y, else the nearest US land cell within `rings` rings; -1 if none."""
        cell = self.grid.cell_at(x, y)
        if cell < 0:
            return -1
        if self.owner[cell] >= 0:
            return cell
        seen, frontier = {cell}, [cell]
        for _ in range(rings):
            ring = [n for c in frontier for n in self.grid.neighbours(c) if n not in seen]
            seen.update(ring)
            land = [n for n in ring if self.owner[n] >= 0]
            if land:
                return min(land, key=lambda n: _d2(self.grid.center(*self.grid.col_row(n)), (x, y)))
            frontier = ring
        return -1

    def place(self, lon: float, lat: float, rings: int = 1):
        x, y = self.proj(lon, lat)
        return x, y, self.land_cell(x, y, rings)

    def county_at(self, x: float, y: float, cell: int | None = None) -> str | None:
        k = self.county_index.locate(x, y)
        if k >= 0:
            return self.county_ids[k]
        if cell is not None and cell >= 0 and self.world["county"][cell] >= 0:  # just offshore: the hex's county
            return self.world["counties"][self.world["county"][cell]]
        return None

    def state_of(self, county: str | None, cell: int, filed: str | None = None) -> str | None:
        """The filed state if it is mapped, else the county's state, else the cell owner's. (wonders.py passes the
        publisher's state; military.py passes none, so the point's own county decides.)"""
        if filed and filed in self.mapped:
            return filed
        if county and self.abbr_by_fips.get(county[:2]) in self.mapped:
            return self.abbr_by_fips[county[:2]]
        return self.abbrs[self.owner[cell]] if cell >= 0 and self.owner[cell] >= 0 else None

    def covered_cells(self, shapes: list[Shape]) -> list[int]:
        """US land cells whose centre lies inside any of the shapes."""
        g, out = self.grid, set()
        dy = 1.5 * g.radius
        for s in shapes:
            x0, y0, x1, y1 = s.bbox
            for row in range(max(0, int(y0 / dy) - 1), min(g.rows, int(y1 / dy) + 2)):
                for col in range(g.cols):
                    cx, cy = g.center(col, row)
                    if x0 <= cx <= x1 and y0 <= cy <= y1 and s.contains(cx, cy):
                        idx = g.index(col, row)
                        if self.owner[idx] >= 0:
                            out.add(idx)
        return sorted(out)

    def settle(self, x: float, y: float, cell: int) -> tuple[float, float]:
        """Rounded x/y, moved to the cell centre when the point is not inside its cell (snapped offshore sites)."""
        x, y = round(x, 1), round(y, 1)
        if self.grid.cell_at(x, y) != cell:
            x, y = (round(v, 1) for v in self.grid.center(*self.grid.col_row(cell)))
        return x, y


def _d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "site"


# --- installations and ranges ------------------------------------------------------------


def _key(site: str, component: str) -> tuple[str, str]:
    return " ".join(site.lower().split()), component


def _tier(area: float | None, comp: str, flagship: bool) -> str:
    if comp in GUARD or comp in RESERVE:
        return "base" if area is not None and area >= BASE_KM2 else "site"
    if flagship or (area is not None and area >= MAJOR_KM2):
        return "major"
    return "base" if area is not None and area >= PARCEL_KM2 else "site"


def _fold(out: list[dict]) -> tuple[list[dict], int]:
    """Fold aliased sites and small parcels into their installation; returns (kept, folded count)."""
    by_key = {_key(i["name"], i["component"]): i for i in out}
    parent_of = {}
    for i in out:
        k = _key(i["name"], i["component"])
        if k in ALIASES and ALIASES[k] in by_key:
            parent_of[i["id"]] = by_key[ALIASES[k]]
    by_cell = defaultdict(list)
    for i in out:
        if i["id"] not in parent_of:
            by_cell[(i["cell"], i["component"])].append(i)
    for group in by_cell.values():
        big = [i for i in group if not i["curated"] and (i["area_km2"] or 0) >= PARCEL_KM2] + \
              [i for i in group if i["curated"]]
        if not big:
            continue
        for i in group:
            if i not in big and not i["curated"] and (i["area_km2"] or 0) < PARCEL_KM2:
                parent_of[i["id"]] = min(big, key=lambda h: _d2((h["x"], h["y"]), (i["x"], i["y"])))  # the nearest
    for i in out:
        head = parent_of.get(i["id"])
        if head is None:
            continue
        head.setdefault("parts", []).append(i["name"])
        if i["area_km2"]:
            head["area_km2"] = float(f"{(head['area_km2'] or 0) + i['area_km2']:.3g}")
        if i["component"] != head["component"]:
            comps = head.setdefault("components", [head["component"]])
            if i["component"] not in comps:
                comps.append(i["component"])
        head["joint"] = head["joint"] or i["joint"]
    kept = [i for i in out if i["id"] not in parent_of]
    for i in kept:
        if "parts" in i:
            i["parts"].sort()
    return kept, len(parent_of)


def installations(loc: Locator, mirta: dict):
    shapes, area, poly_meta = defaultdict(list), defaultdict(float), {}
    flagships = {_key(n, c) for n, c in FLAGSHIPS}
    for p in mirta["polygons"]:
        k = _key(p["site"], p["component"])
        area[k] += p["area_km2"]
        poly_meta.setdefault(k, p)
        if p["rings"]:
            shapes[k].append(Shape([[loc.proj(lon, lat) for lon, lat in r] for r in p["rings"]]))

    out, ids, dropped = [], Counter(), Counter()
    for p in mirta["points"]:
        if p["status"] not in SHOWN_STATUS:
            dropped["closed or excess"] += 1
            continue
        if p["state"] not in loc.mapped:
            dropped["outside the lower 48"] += 1
            continue
        x, y, cell = loc.place(p["lon"], p["lat"], rings=2)
        if cell < 0:
            dropped["off the map"] += 1
            continue
        k = _key(p["site"], p["component"])
        a = float(f"{area[k]:.3g}") if area.get(k) else None  # 3 significant digits: tiny sites stay > 0
        cells = loc.covered_cells(shapes[k]) if shapes.get(k) and a and a >= 200 else None
        if cells == []:
            cells = None
        county = loc.county_at(*loc.proj(p["lon"], p["lat"]), cell)
        comp = p["component"]
        curated = k in flagships
        tier = _tier(a, comp, curated)
        base_id = slug(p["site"])[:40].rstrip("-") + "-" + SHORT[comp]
        ids[base_id] += 1
        xs, ys = loc.settle(x, y, cell)
        out.append({
            "id": base_id if ids[base_id] == 1 else f"{base_id}-{ids[base_id]}",
            "name": p["site"], "component": comp, "joint": bool(p["joint"]), "status": p["status"], "tier": tier,
            "curated": curated, "area_km2": a, "cells": cells, "x": xs, "y": ys, "cell": cell, "county": county,
            "cbsa": loc.cbsa_of.get(county) if county else None,
            "state": loc.state_of(county, cell),  # where the point is, not where MIRTA files it
            "state_filed": p["state"],
        })
    found = {_key(i["name"], i["component"]) for i in out}
    missing = [n for n, c in FLAGSHIPS if _key(n, c) not in found]
    if missing:
        print(f"    WARNING: flagship installations not found in MIRTA: {missing}")
    out, folded = _fold(out)
    dropped["folded into a larger installation"] = folded
    for i in out:  # a fold can push an active-duty site past the parcel size
        i["tier"] = _tier(i["area_km2"], i["component"], i["curated"])
    out.sort(key=lambda i: (TIERS.index(i["tier"]), -(i["area_km2"] or 0), i["name"]))
    return out, dropped, shapes, area, poly_meta


def ranges(loc: Locator, shapes, area, poly_meta, inst_by_key) -> list[dict]:
    shapes, area, comps = dict(shapes), dict(area), {}
    for k, head in ALIASES.items():  # one range filed as several sites
        if k in area and head in area:
            area[head] += area.pop(k)
            shapes[head] = list(shapes.get(head, [])) + list(shapes.pop(k, []))
            comps.setdefault(head, [head[1]]).append(k[1])
    cand = [(a, k) for k, a in area.items()
            if a >= RANGE_MIN_KM2 and poly_meta[k]["status"] in ("act", "semi") and shapes.get(k)]
    cand.sort(key=lambda t: -t[0])
    out = []
    for a, k in cand:
        cells = loc.covered_cells(shapes[k])
        inst = inst_by_key.get(k)
        if not cells:
            if inst is None:
                continue  # neither a covered hex nor a mapped point: outside the lower 48
            cells = [inst["cell"]]
        if inst is None and not all(loc.abbrs[loc.owner[c]] in loc.mapped for c in cells):
            continue
        meta = poly_meta[k]
        if meta["state"] not in loc.mapped and inst is None:
            # e.g. a range filed under AK/HI whose outline cannot be in the lower 48: covered_cells would be empty
            continue
        states = Counter(loc.abbrs[loc.owner[c]] for c in cells)
        out.append({"id": slug(meta["site"])[:40].rstrip("-") + "-" + SHORT[meta["component"]], "name": meta["site"],
                    "component": meta["component"], "area_km2": float(f"{a:.4g}"), "cells": cells,
                    # the installation point's state when it is mapped (Fort Bliss: HQ in TX though most hexes are NM), else
                    # where most of its hexes are (UTTR is filed NV)
                    "state": inst["state"] if inst and inst.get("state") else states.most_common(1)[0][0],
                    "hex_state": states.most_common(1)[0][0],
                    "installation": inst["id"] if inst else None,
                    **({"components": sorted(set(comps[k]), key=comps[k].index)} if k in comps else {})})
        if len(out) == RANGES_N:
            break
    return out


# --- states -------------------------------------------------------------------------------


def _county_key(name: str) -> str:
    n = " ".join(name.lower().replace(".", "").split())
    for suf in (" city and borough", " census area", " municipality", " borough", " county", " parish"):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def _match_counties(loc: Locator) -> dict[tuple[str, str], str | None]:
    """(state abbr, normalised county name) → GEOID on the map (None when ambiguous)."""
    world = set(loc.world["counties"])
    out: dict = {}
    for c in loc.counties_by_name:
        if c["geoid"] not in world:
            continue
        k = (loc.abbr_by_fips.get(c["state_fips"]), _county_key(c["name"]))
        out[k] = None if k in out and out[k] != c["geoid"] else c["geoid"]
    return out


def _r(v, nd=0):
    if v is None:
        return None
    return int(round(v)) if nd == 0 else round(v, nd)


def states_block(loc: Locator, dmdc: dict, oldcc: dict):
    name_to_abbr = loc.state_names
    out: dict[str, dict] = {}
    unmatched = []
    for name, rec in dmdc["states"].items():
        abbr = name_to_abbr.get(name.upper())
        if not abbr:
            unmatched.append(name)
            continue
        out.setdefault(abbr, {})["personnel"] = rec
    match = _match_counties(loc)
    county_hits = county_total = 0
    for name, rec in oldcc["states"].items():
        abbr = name_to_abbr.get(name.upper())
        if not abbr:
            unmatched.append(name)
            continue
        tops = []
        for c in oldcc["counties"].get(name, []):
            geoid = match.get((abbr, _county_key(c["county"])))
            county_total += 1
            county_hits += geoid is not None
            nm = c["county"]
            tops.append({"name": nm, "county": geoid, "contracts": _r(c["contracts"])})
        out.setdefault(abbr, {})["spending"] = {
            "total": _r(rec["total"]), "contracts": _r(rec["contracts"]), "grants": _r(rec["grants"]),
            "payroll": _r(rec["payroll"]),
            "payroll_parts": {"civilian": _r(rec["civilian_pay"]), "active_duty": _r(rec["active_duty_pay"]),
                              "guard_reserve": _r(rec["guard_reserve_pay"])},
            "pct_gdp": _r(rec["pct_gdp"], 4), "per_resident": _r(rec["per_resident"]),
            "personnel": _r(rec["personnel"]),
            "top_counties": tops,
            "top_vendors": [{"name": v["name"], "contracts": _r(v["contracts"])}
                            for v in oldcc["vendors"].get(name, [])[:5]],
        }
    for st in out.values():
        st.setdefault("personnel", None)
        st.setdefault("spending", None)
    return dict(sorted(out.items())), unmatched, (county_hits, county_total)


# --- cities -------------------------------------------------------------------------------


def acs_sum(acs: dict, geoids, key: str):
    vals = [acs.get(f"0500000US{g}", {}).get(key) for g in geoids]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def cities_block(loc: Locator, inst: list[dict], rngs: list[dict], acs: dict) -> dict:
    by_county = defaultdict(list)
    for i in inst:
        if i["status"] == "act" and i["component"] not in GUARD and i["tier"] != "site" and i["county"]:
            by_county[i["county"]].append(i)
    range_cells = {c: r for r in rngs for c in r["cells"]}
    inst_by_id = {i["id"]: i for i in inst}
    out = {}
    for cid, city in loc.cities.items():
        members = city.get("counties") or []
        sites = [i for g in members for i in by_county.get(g, [])]
        for c in city.get("footprint") or []:
            r = range_cells.get(c)
            if r and r.get("installation") in inst_by_id and inst_by_id[r["installation"]] not in sites:
                sites.append(inst_by_id[r["installation"]])
        if not sites:
            continue
        af, pop16 = acs_sum(acs, members, "B23025_006"), acs_sum(acs, members, "B23025_001")
        if af is None or not pop16:
            continue
        share = af / pop16
        if share < AF_SHARE_MIN and not (af >= AF_COUNT_MIN and share >= AF_COUNT_SHARE):
            continue
        sites.sort(key=lambda i: (TIERS.index(i["tier"]), -(i["area_km2"] or 0)))
        naval = sum(TIER_WEIGHT[i["tier"]] for i in sites if i["component"] in NAVAL)
        land_air = sum(TIER_WEIGHT[i["tier"]] for i in sites
                       if i["component"] not in NAVAL and i["component"] != "other")
        out[cid] = {"tier": "fortress" if share >= FORTRESS_SHARE else "garrison", "armed_forces": int(af),
                    "share": round(share, 4), "naval": naval > land_air,
                    "installations": [i["id"] for i in sites[:8]], "installation_count": len(sites)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["share"]))


# --- build --------------------------------------------------------------------------------


def build() -> None:
    world = load_interim("_world")
    loc = Locator(world)
    mirta_p = load_interim_payload("mirta")
    mirta = mirta_p["data"]
    dmdc_p, oldcc_p, acs_p = (load_interim_payload(n) for n in ("dmdc_personnel", "oldcc_spending", "acs_5yr"))
    acs = acs_p["data"]["geos"]

    inst, dropped, shapes, area, poly_meta = installations(loc, mirta)
    inst_by_key = {}
    for i in inst:
        inst_by_key.setdefault(_key(i["name"], i["component"]), i)
    rngs = ranges(loc, shapes, area, poly_meta, inst_by_key)
    states, unmatched, (hits, total) = states_block(loc, dmdc_p["data"], oldcc_p["data"])
    cities = cities_block(loc, inst, rngs, acs)
    renamed = apply_renames(inst) + apply_renames(rngs)

    us_geos = [g for g in acs if g.startswith("0500000US") and g[9:11] != "72"]  # 50 states + DC
    vets = sum(acs[g].get("B21001_002") or 0 for g in us_geos)
    af = sum(acs[g].get("B23025_006") or 0 for g in us_geos)
    oldcc_total = oldcc_p["data"]["total"] or {}
    year = acs_p["data"].get("year")
    doc = {
        "version": 1,
        "vintage": {"installations": mirta_p.get("vintage") or "DoD MIRTA FY2025",
                    "personnel": dmdc_p.get("vintage"), "spending": oldcc_p.get("vintage"),
                    "acs": acs_p.get("vintage") or f"ACS {year} 5-year"},
        "sources": ["mirta", "dmdc_personnel", "oldcc_spending", "acs_5yr"],
        "rule": CITY_RULE,
        "rules": {"tier": TIER_RULE, "ranges": RANGE_RULE, "encampment": CITY_RULE,
                  "installations": "MIRTA installation points in the lower 48 that are active, semi-active or in "
                                   "caretaker status (closed and excess sites are left out). A point just offshore is "
                                   "placed on the nearest US land hex. Large boundaries (200 km² or more) also "
                                   "list the hexes whose centre they cover. Each site is placed in the state where "
                                   "its point lies, which can differ from the state MIRTA files it under.",
                  "names": RENAME_RULE},
        "flagships": [n for n, _ in FLAGSHIPS],
        "caveats": CAVEATS,
        "components": COMPONENTS,
        "installations": inst,
        "ranges": rngs,
        "states": states,
        "national": {
            "personnel": dmdc_p["data"]["us_total"],
            "personnel_scope": "50 states, DC and US territories (DMDC 'United States' total)",
            "spending": {"total": _r(oldcc_total.get("total")), "contracts": _r(oldcc_total.get("contracts")),
                         "grants": _r(oldcc_total.get("grants")), "payroll": _r(oldcc_total.get("payroll")),
                         "pct_gdp": _r(oldcc_total.get("pct_gdp"), 4),
                         "per_resident": _r(oldcc_total.get("per_resident")), "scope": "50 states and DC"},
            "veterans": int(vets) if vets else None,
            "armed_forces_resident": int(af) if af else None,
            "acs_scope": "50 states and DC, ACS 5-year: veterans are civilians 18+ (B21001_002); armed forces are "
                         "residents 16+ on active duty (B23025_006)",
        },
        "cities": cities,
    }
    write_web("military.json", doc)
    tiers = Counter(i["tier"] for i in inst)
    print(f"    {len(inst)} installations ({', '.join(f'{v} {k}' for k, v in tiers.items())}); "
          f"left out: {dict(dropped)}")
    print(f"    renamed {renamed} rows to their current official names (2025 Army renames)")
    print(f"    {len(rngs)} ranges ({sum(len(r['cells']) for r in rngs)} hexes); {len(cities)} Encampment cities "
          f"({sum(c['tier'] == 'fortress' for c in cities.values())} fortress)")
    print(f"    {len(states)} states; OLDCC top counties matched to the map {hits}/{total}"
          + (f"; unmatched state names {unmatched}" if unmatched else ""))


# --- check --------------------------------------------------------------------------------


def _load(web_dir: Path, name: str):
    p = Path(web_dir) / name
    return json.loads(p.read_text()) if p.exists() else None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(web_dir) -> list[tuple[str, str]]:
    path = Path(web_dir) / "military.json"
    if not path.exists():
        return [("NOTE", "military.json not built (optional)")]
    out: list[tuple[str, str]] = []
    bad = []
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

    if d.get("version") != 1:
        bad.append("version must be 1")
    if not isinstance(d.get("sources"), list) or not d.get("sources"):
        bad.append("sources must be a non-empty list")
    if not isinstance(d.get("rule"), str):
        bad.append("rule (Encampment rule text) missing")
    comp_ids = [c.get("id") for c in d.get("components") or []]
    if not comp_ids:
        bad.append("components missing")
    ids = set()
    for i in d.get("installations") or []:
        tag = f"installation {i.get('id')!r}"
        if not isinstance(i.get("id"), str) or i["id"] in ids:
            bad.append(f"{tag}: missing or duplicate id")
        ids.add(i.get("id"))
        if i.get("component") not in comp_ids:
            bad.append(f"{tag}: unknown component {i.get('component')!r}")
        if i.get("status") not in STATUSES or i.get("tier") not in TIERS or not isinstance(i.get("joint"), bool):
            bad.append(f"{tag}: bad status/tier/joint")
        if i.get("area_km2") is not None and not (_is_num(i["area_km2"]) and i["area_km2"] > 0):
            bad.append(f"{tag}: area_km2 must be > 0 or null")
        if i.get("cells") is not None and (not isinstance(i["cells"], list) or not i["cells"]
                                           or not all(cell_ok(c) for c in i["cells"])):
            bad.append(f"{tag}: cells must be null or a non-empty list of US land cells")
        if not cell_ok(i.get("cell")) or not _is_num(i.get("x")) or not _is_num(i.get("y")):
            bad.append(f"{tag}: bad cell/x/y")
        if i.get("county") is not None and counties and i["county"] not in counties:
            bad.append(f"{tag}: county {i['county']} not in world.json")
        if i.get("cbsa") is not None and city_ids and i["cbsa"] not in city_ids:
            bad.append(f"{tag}: cbsa {i['cbsa']} not in cities.json")
    for r in d.get("ranges") or []:
        if not r.get("cells") or not all(cell_ok(c) for c in r["cells"]) or not _is_num(r.get("area_km2")):
            bad.append(f"range {r.get('id')!r}: needs area_km2 and non-empty US land cells")
        if r.get("installation") is not None and r["installation"] not in ids:
            bad.append(f"range {r.get('id')!r}: unknown installation")
    for abbr, st in (d.get("states") or {}).items():
        p = st.get("personnel")
        if p is not None:
            for grp in ("active", "guard_reserve", "civilian"):
                vals = (p.get(grp) or {}).values()
                if not all(v is None or (isinstance(v, int) and v >= 0) for v in vals):
                    bad.append(f"state {abbr}: personnel.{grp} must be ints >= 0 or null")
        s = st.get("spending")
        if s is not None:
            if s.get("total") is not None and not (_is_num(s["total"]) and s["total"] >= 0):
                bad.append(f"state {abbr}: spending.total must be >= 0 (US dollars)")
            if s.get("pct_gdp") is not None and not 0 <= s["pct_gdp"] <= 1:
                bad.append(f"state {abbr}: pct_gdp must be a 0..1 share")
            for c in s.get("top_counties") or []:
                if c.get("county") is not None and counties and c["county"] not in counties:
                    bad.append(f"state {abbr}: top county {c['county']} not in world.json")
    for cid, c in (d.get("cities") or {}).items():
        if city_ids and cid not in city_ids:
            bad.append(f"city {cid}: not in cities.json")
        if c.get("tier") not in ("garrison", "fortress") or not (_is_num(c.get("share")) and 0 <= c["share"] <= 1):
            bad.append(f"city {cid}: bad tier/share")
        if not all(x in ids for x in c.get("installations") or []):
            bad.append(f"city {cid}: unknown installation id")
    for m in bad[:12]:
        out.append(("FAIL", f"military.json: {m}"))
    if len(bad) > 12:
        out.append(("FAIL", f"military.json: … and {len(bad) - 12} more problems"))
    size = path.stat().st_size
    out.append(("WARN" if size > 300_000 else "PASS", f"military.json {size / 1e3:.0f} kB (eager budget ~300 kB)"))
    if not bad:
        inst = d.get("installations") or []
        tiers = Counter(i["tier"] for i in inst)
        cs = d.get("cities") or {}
        out.append(("PASS", f"military.json: {len(inst)} installations ({tiers.get('major', 0)} major, "
                            f"{tiers.get('base', 0)} base, {tiers.get('site', 0)} site), {len(d.get('ranges') or [])} "
                            f"ranges, {len(d.get('states') or {})} states, {len(cs)} Encampment cities "
                            f"({sum(c['tier'] == 'fortress' for c in cs.values())} fortress)"))
        no_sp = [a for a, s in (d.get("states") or {}).items() if s.get("spending") is None]
        if no_sp:
            out.append(("NOTE", f"military.json: no OLDCC spending for {', '.join(no_sp)}"))
    return out
