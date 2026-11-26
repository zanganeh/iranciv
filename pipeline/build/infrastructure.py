"""Tile improvements: Interstates, major highways and railroads rasterised onto hex links, plus ports and airports.

Lines are never shipped as polylines. Each Natural Earth 1:10m line is sampled every SAMPLE_KM along its length,
each sample is mapped to its hex, repeats in a row are dropped, a cell whose neighbours on the line already touch is
dropped (the line only clipped its corner; this keeps a road along a row boundary from zigzagging), and every step between two cells becomes a link
between neighbouring cells (a step that skips cells is filled with a hex line walk). The browser draws a link as a
road or rail segment from hex centre to hex centre.

Filtering keeps the map readable: all Interstates; other roads only at scale rank <= MAJOR_MAX_SCALERANK, or
expressways Natural Earth types as a "Major Highway" or "Beltway"; rail at scale rank <= RAIL_MAX_SCALERANK
(tier 0 "main line" at <= RAIL_MAIN_SCALERANK). A major-highway link that already carries an Interstate is dropped.
Rail samples inside Canada or Mexico are dropped (the rail layer has no country field).

Ports: the USACE principal ports (port-area centroid). Airports: every primary airport (FAA hub class L/M/S/N)
plus every airport in FAA's all-cargo table. A site's `cell` is the US land cell at its point, or the nearest one
up to SITE_RINGS hexes away (port areas on rivers and bays); x/y stay the true position.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from ..core.geo import MapProjection, Shape, ShapeIndex
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["_world", "natural_earth_roads", "natural_earth", "usace_ports", "faa_airports", "census_boundaries",
            "cbsa_delineation", "cities.json"]
PRODUCES = ["infrastructure.json"]

SAMPLE_KM = 4.0
MAJOR_MAX_SCALERANK = 6
MAJOR_TYPES = {"Major Highway", "Beltway"}
RAIL_MAX_SCALERANK = 7
RAIL_MAIN_SCALERANK = 5
SITE_RINGS = 2
ROAD_CLASSES = [{"id": "interstate", "name": "Interstate"}, {"id": "major", "name": "Major highway"}]
RAIL_TIERS = [{"id": "main", "name": "Main line"}, {"id": "secondary", "name": "Secondary line"}]
HUBS = {"L": "Large hub", "M": "Medium hub", "S": "Small hub", "N": "Nonhub primary"}

RULES = {
    "roads": "Natural Earth 1:10m roads in the US, sampled every 4 km and snapped to hex-centre links. Class 0: every "
             "Interstate. Class 1: other roads at Natural Earth scale rank "
             f"{MAJOR_MAX_SCALERANK} or less (its most prominent US and state highways) plus every expressway Natural "
             "Earth marks as a major highway or beltway, where no Interstate already joins the same two hexes.",
    "rail": "Natural Earth 1:10m railroads at scale rank 7 or less, sampled every 4 km and snapped to hex-centre links; "
            f"tier 0 (main line) is scale rank {RAIL_MAIN_SCALERANK} or less. Lines in Canada and Mexico are clipped. "
            "Natural Earth holds about half of the real US rail network and carries no owner or traffic data.",
    "ports": "The USACE principal ports by total tonnage (short tons). Each is a port statistical area, placed at the "
             "area's centroid, so long river ports are approximate.",
    "airports": "Every primary airport (FAA hub class large, medium, small or nonhub: 10,000+ boardings a year) and "
                "every airport in FAA's all-cargo table, placed by FAA location id with the NASR airport master.",
}


# ------------------------------------------------------------------ hex helpers


def _axial(grid: HexGrid, cell: int) -> tuple[int, int]:
    col, row = grid.col_row(cell)
    return col - (row - (row & 1)) // 2, row


def _from_axial(grid: HexGrid, q: int, r: int) -> int:
    col = q + (r - (r & 1)) // 2
    if 0 <= col < grid.cols and 0 <= r < grid.rows:
        return grid.index(col, r)
    return -1


def hex_distance(grid: HexGrid, a: int, b: int) -> int:
    (q1, r1), (q2, r2) = _axial(grid, a), _axial(grid, b)
    return (abs(q1 - q2) + abs(r1 - r2) + abs((q1 + r1) - (q2 + r2))) // 2


def hex_walk(grid: HexGrid, a: int, b: int) -> list[int]:
    """Cells on the hex line from a to b, both included (cube lerp with a tiny nudge to break ties)."""
    n = hex_distance(grid, a, b)
    if n <= 1:
        return [a, b] if a != b else [a]
    (q1, r1), (q2, r2) = _axial(grid, a), _axial(grid, b)
    out = []
    for i in range(n + 1):
        t = i / n
        x = q1 + (q2 - q1) * t + 1e-6
        z = r1 + (r2 - r1) * t + 1e-6
        y = -x - z
        rx, ry, rz = round(x), round(y), round(z)
        dx, dy, dz = abs(rx - x), abs(ry - y), abs(rz - z)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy <= dz:
            rz = -rx - ry
        c = _from_axial(grid, rx, rz)
        if c >= 0 and (not out or out[-1] != c):
            out.append(c)
    return out


def sample_cells(grid: HexGrid, pts: list[tuple[float, float]], keep=None) -> list[list[int]]:
    """Runs of distinct consecutive cells along a projected line, sampled every SAMPLE_KM. `keep(x, y)` False
    breaks the run (the sample is dropped)."""
    runs, run = [], []

    def add(x, y):
        nonlocal run
        if keep is not None and not keep(x, y):
            if len(run) > 1:
                runs.append(run)
            run = []
            return
        c = grid.cell_at(x, y)
        if c < 0:
            return
        if not run or run[-1] != c:
            run.append(c)

    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / SAMPLE_KM))
        for i in range(steps):
            t = i / steps
            add(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
    if pts:
        add(*pts[-1])
    if len(run) > 1:
        runs.append(run)
    return runs


def straighten(grid: HexGrid, run: list[int]) -> list[int]:
    """Drop detour cells: where the cells before and after a cell are neighbours themselves, the line only clipped
    that cell's corner. Without this, a road running along a row boundary zigzags between two rows."""
    out = []
    for c in run:
        while len(out) >= 2 and c in grid.neighbours(out[-2]):
            out.pop()
        if not out or out[-1] != c:
            out.append(c)
    return out


def links_of(grid: HexGrid, run: list[int]) -> list[tuple[int, int]]:
    out = []
    run = straighten(grid, [c for path in ([run[0]], *(hex_walk(grid, a, b)[1:] for a, b in zip(run, run[1:])))
                            for c in path])
    for a, b in zip(run, run[1:]):
        path = hex_walk(grid, a, b)
        for u, v in zip(path, path[1:]):
            if u != v:
                out.append((u, v) if u < v else (v, u))
    return out


# ------------------------------------------------------------------ roads and rail


def _route_name(level: str | None, name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    if level == "Interstate":
        return name if name.upper().startswith("I") else f"I-{name}"
    if level == "Federal":
        return name if name.upper().startswith("US") else f"US {name}"
    return name


def _route_key(name: str) -> tuple:
    digits = "".join(ch for ch in name if ch.isdigit())
    return (len(digits) or 9, int(digits) if digits else 999, name)


def build_roads(grid, proj, owner, ne):
    cls_of = defaultdict(lambda: 9)
    names = defaultdict(Counter)
    for f in ne["roads"]:
        inter = f["level"] == "Interstate"
        sr = f["scalerank"] if f["scalerank"] is not None else 99
        major = sr <= MAJOR_MAX_SCALERANK or (f["expressway"] and f["type"] in MAJOR_TYPES)
        if not (inter or major):
            continue
        cls = 0 if inter else 1
        name = _route_name(f["level"], f["name"])
        for line in f["lines"]:
            pts = [proj(lon, lat) for lon, lat in line]
            for run in sample_cells(grid, pts):
                for a, b in links_of(grid, run):
                    if owner[a] < 0 and owner[b] < 0:
                        continue
                    if owner[a] == -2 or owner[b] == -2:
                        continue  # into Canada or Mexico
                    if cls < cls_of[(a, b)]:
                        cls_of[(a, b)] = cls
                        names[(a, b)].clear()
                    if cls == cls_of[(a, b)] and name:
                        names[(a, b)][name] += 1
    roads = []
    for (a, b), cls in sorted(cls_of.items()):
        nm = min(names[(a, b)], key=_route_key) if names[(a, b)] else None
        roads.append([a, b, cls, nm])
    return roads


def build_rail(grid, proj, owner, ne, foreign_idx):
    tier_of = {}

    def keep(x, y):
        return foreign_idx.locate(x, y) < 0

    for f in ne["rail"]:
        sr = f["scalerank"] if f["scalerank"] is not None else 99
        if sr > RAIL_MAX_SCALERANK:
            continue
        tier = 0 if sr <= RAIL_MAIN_SCALERANK else 1
        for line in f["lines"]:
            pts = [proj(lon, lat) for lon, lat in line]
            for run in sample_cells(grid, pts, keep):
                for a, b in links_of(grid, run):
                    if owner[a] == -2 or owner[b] == -2 or (owner[a] < 0 and owner[b] < 0):
                        continue
                    tier_of[(a, b)] = min(tier, tier_of.get((a, b), 9))
    return [[a, b, t] for (a, b), t in sorted(tier_of.items())]


# ------------------------------------------------------------------ sites


class Locator:
    def __init__(self, world, grid, proj):
        self.grid, self.proj, self.world = grid, proj, world
        self.owner, self.county, self.geoids = world["owner"], world["county"], world["counties"]
        bounds = load_interim("census_boundaries")["states"]
        abbr_of = {s["fips"]: s["abbr"] for s in bounds}
        self.abbrs = [abbr_of.get(f) for f in world["states"]]
        city_ids = {c["id"] for c in json.loads((WEB_DATA / "cities.json").read_text())}
        self.cbsa_of = {g: code for code, geoids in load_interim("cbsa_delineation")["counties"].items()
                        if code in city_ids for g in geoids}

    def land_cell(self, x, y, rings=SITE_RINGS) -> int:
        grid, owner = self.grid, self.owner
        cell = grid.cell_at(x, y)
        if cell < 0:
            return -1
        if owner[cell] >= 0:
            return cell
        seen, frontier = {cell}, [cell]
        for _ in range(rings):
            ring = [n for c in frontier for n in grid.neighbours(c) if n not in seen]
            ring = list(dict.fromkeys(ring))
            seen.update(ring)
            land = [n for n in ring if owner[n] >= 0]
            if land:
                return min(land, key=lambda n: math.dist(grid.center(*grid.col_row(n)), (x, y)))
            frontier = ring
        return -1

    def place(self, lon, lat) -> dict | None:
        if lon is None or lat is None:
            return None
        x, y = self.proj(lon, lat)
        cell = self.land_cell(x, y)
        if cell < 0:
            return None
        ci = self.county[cell]
        geoid = self.geoids[ci] if ci >= 0 else None
        return {"x": round(x, 1), "y": round(y, 1), "cell": cell, "county": geoid,
                "cbsa": self.cbsa_of.get(geoid) if geoid else None, "state": self.abbrs[self.owner[cell]]}


def build_ports(loc: Locator, usace: dict) -> list[dict]:
    out = []
    for p in usace["ports"]:
        at = loc.place(p["lon"], p["lat"])
        if at is None:
            continue  # Alaska, Hawaii, Puerto Rico, Guam ...
        out.append({"id": p["id"], "name": p["name"], "type": p["type"], "rank": p["rank"], "tons": p["tons"],
                    "domestic": p["domestic"], "foreign": p["foreign"], "imports": p["imports"],
                    "exports": p["exports"], "year": usace["year"], **at})
    return out


def build_airports(loc: Locator, faa: dict) -> list[dict]:
    out = []
    for a in faa["airports"]:
        if a["hub"] is None and a["cargo_lbs"] is None:
            continue
        at = loc.place(a["lon"], a["lat"])
        if at is None:
            continue
        out.append({"id": a["id"], "name": a["name"], "city": a["city"], "hub": a["hub"],
                    "enplanements": _int(a["enplanements"]), "enplanements_prev": _int(a["enplanements_prev"]),
                    "cargo_lbs": _int(a["cargo_lbs"]), "year": faa["year"], **at})
    out.sort(key=lambda a: -(a["enplanements"] or 0))
    return out


def _int(v):
    return None if v is None else int(round(v))


# ------------------------------------------------------------------ build


def build() -> None:
    world = load_interim("_world")
    grid, proj, owner = HexGrid(**world["grid"]), MapProjection(**world["projection"]), world["owner"]
    ne = load_interim("natural_earth_roads")
    foreign = load_interim("natural_earth")["countries"]
    foreign_idx = ShapeIndex([Shape([[proj(lon, lat) for lon, lat in r] for r in c["rings"]]) for c in foreign],
                             cell_km=100)
    roads = build_roads(grid, proj, owner, ne)
    rail = build_rail(grid, proj, owner, ne, foreign_idx)

    loc = Locator(world, grid, proj)
    usace = load_interim_payload("usace_ports")
    faa = load_interim_payload("faa_airports")
    ports = build_ports(loc, usace["data"])
    airports = build_airports(loc, faa["data"])

    ne_vintage = load_interim_payload("natural_earth_roads")["vintage"]
    doc = {
        "version": 1,
        "vintage": {"roads": ne_vintage, "rail": ne_vintage, "ports": usace["vintage"], "airports": faa["vintage"]},
        "sources": ["natural_earth_roads", "usace_ports", "faa_airports"],
        "rules": RULES,
        "road_classes": ROAD_CLASSES,
        "rail_tiers": RAIL_TIERS,
        "hubs": HUBS,
        "roads": roads,
        "rail": rail,
        "ports": ports,
        "airports": airports,
    }
    write_web("infrastructure.json", doc)
    road_cells = {c for a, b, *_ in roads for c in (a, b) if owner[c] >= 0}
    rail_cells = {c for a, b, _ in rail for c in (a, b) if owner[c] >= 0}
    land = sum(1 for o in owner if o >= 0)
    n0 = sum(1 for r in roads if r[2] == 0)
    print(f"    roads: {n0} Interstate + {len(roads) - n0} major links on {len(road_cells)} of {land} land hexes; "
          f"rail: {len(rail)} links on {len(rail_cells)} hexes; {len(ports)} ports, {len(airports)} airports")


# ------------------------------------------------------------------ check


def _neighbours(grid_doc):
    return HexGrid(grid_doc["radius"], grid_doc["cols"], grid_doc["rows"])


def check(web_dir: Path) -> list[tuple[str, str]]:
    path = Path(web_dir) / "infrastructure.json"
    if not path.exists():
        return [("NOTE", "infrastructure.json not built (optional; run: build infrastructure --deps)")]
    out = []
    doc = json.loads(path.read_text())
    for k in ("version", "vintage", "sources", "road_classes", "roads", "rail", "ports", "airports"):
        if k not in doc:
            out.append(("FAIL", f"infrastructure.json lacks '{k}'"))
    if any(lv == "FAIL" for lv, _ in out):
        return out
    if doc["version"] != 1:
        out.append(("FAIL", f"infrastructure.json version {doc['version']!r} (expected 1)"))
    wpath = Path(web_dir) / "world.json"
    world = json.loads(wpath.read_text()) if wpath.exists() else None
    if not world or "grid" not in world:
        return out + [("NOTE", "infrastructure.json: world.json unavailable, cell checks skipped")]
    grid = _neighbours(world["grid"])
    owner = world.get("owner") or []
    n = grid.size
    county_ids = {c["geoid"] for c in world.get("counties", [])}
    cpath = Path(web_dir) / "cities.json"
    city_ids = {c["id"] for c in json.loads(cpath.read_text())} if cpath.exists() else None
    spath = Path(web_dir) / "sources.json"
    if spath.exists():
        src = json.loads(spath.read_text())
        ids = {s["id"] for s in (src.get("sources", src) if isinstance(src, dict) else src) if isinstance(s, dict)}
        miss = [s for s in doc["sources"] if s not in ids]
        if ids and miss:
            out.append(("WARN", f"infrastructure.json cites sources missing from sources.json: {miss}"))

    def bad_link(link, ncls):
        if not isinstance(link, list) or len(link) < 3:
            return "shape"
        a, b, c = link[:3]
        if not all(isinstance(v, int) for v in (a, b, c)):
            return "types"
        if not (0 <= a < b < n):
            return "order/range"
        if b not in grid.neighbours(a):
            return "not neighbours"
        if len(owner) == n and owner[a] < 0 and owner[b] < 0:
            return "no US land end"
        if not 0 <= c < ncls:
            return "class"
        return None

    for key, ncls in (("roads", len(doc["road_classes"])), ("rail", len(doc.get("rail_tiers") or [0, 1]))):
        links = doc[key]
        bad = [(l, why) for l in links if (why := bad_link(l, ncls))]
        pairs = [tuple(l[:2]) for l in links if isinstance(l, list)]
        dup = len(pairs) - len(set(pairs))
        if bad or dup:
            out.append(("FAIL", f"infrastructure.json {key}: {len(bad)} bad links (e.g. {bad[:2]}), {dup} duplicates"))
        else:
            cells = {c for l in links for c in l[:2]}
            out.append(("PASS", f"infrastructure.json {key}: {len(links)} neighbour links on {len(cells)} hexes"))
    if any(not (r[3] is None or isinstance(r[3], str)) for r in doc["roads"] if len(r) > 3):
        out.append(("FAIL", "infrastructure.json roads: route name must be a string or null"))

    def check_sites(key, num_keys):
        probs = []
        for s in doc[key]:
            c = s.get("cell")
            if not isinstance(c, int) or not 0 <= c < n or (len(owner) == n and owner[c] < 0):
                probs.append(f"{s.get('id')}: cell {c!r} is not US land")
                continue
            if any(s.get(k) is not None and not isinstance(s.get(k), (int, float)) for k in num_keys):
                probs.append(f"{s.get('id')}: non-numeric value")
            cx, cy = grid.center(*grid.col_row(c))
            if not all(isinstance(s.get(k), (int, float)) for k in ("x", "y")) or \
                    math.dist((cx, cy), (s["x"], s["y"])) > (SITE_RINGS + 1) * world["grid"]["radius"] * 2:
                probs.append(f"{s.get('id')}: x/y far from its cell")
            if s.get("county") is not None and county_ids and s["county"] not in county_ids:
                probs.append(f"{s.get('id')}: unknown county {s['county']}")
            if s.get("cbsa") is not None and city_ids is not None and s["cbsa"] not in city_ids:
                probs.append(f"{s.get('id')}: unknown cbsa {s['cbsa']}")
        ids = [s.get("id") for s in doc[key]]
        if len(ids) != len(set(ids)):
            probs.append("duplicate ids")
        return probs

    for key, nums in (("ports", ("tons", "domestic", "foreign", "imports", "exports", "rank")),
                      ("airports", ("enplanements", "enplanements_prev", "cargo_lbs"))):
        probs = check_sites(key, nums)
        if probs:
            out.append(("FAIL", f"infrastructure.json {key}: {len(probs)} problems, e.g. {probs[:3]}"))
        else:
            metro = sum(1 for s in doc[key] if s.get("cbsa"))
            out.append(("PASS", f"infrastructure.json {key}: {len(doc[key])} sites, {metro} in a mapped metro"))
    hubs = Counter(a.get("hub") for a in doc["airports"])
    out.append(("NOTE", "infrastructure.json airports by hub: " + ", ".join(f"{k or 'cargo-only'} {v}" for k, v in
                                                                         sorted(hubs.items(), key=lambda kv: str(kv[0])))))
    size = path.stat().st_size
    if size > 300_000:
        out.append(("WARN", f"infrastructure.json is {size / 1e3:.0f} kB (eager files should stay under ~300 kB)"))
    return out
