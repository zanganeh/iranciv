"""Trade routes: the largest domestic freight flows between FAF zones, as caravan routes between cities.

FAF5 (`faf5` interim) estimates freight between 132 zones: a metro area's part of one state, or the rest of a
state. Here:

* Alaska and Hawaii are left out (they are off the map).
* Twin zones, the parts of one metro area split by a state line (New York-Newark in NY, NJ, CT and PA), are merged
  into one zone, listing their FAF codes in `faf`.
* Only flows that cross a state line count toward a route: a flow between two FAF parts in the same state (Houston to
  the rest of Texas, or the New Jersey part of New York to the New Jersey part of Philadelphia) is left out, so a
  route is interstate commerce only.
* Zones that have a state in common are never a route (New York-Newark, which reaches into New Jersey, and the
  rest of New Jersey).
* A route needs its two zones' anchors at least MIN_KM apart, so neighbouring metros do not crowd the map.
* The TOP_N largest by value (million current dollars) are kept.

Each zone is anchored on its biggest city's banner cell (cities.json), or for a rest-of-state zone with no city
in cities.json, the land cell nearest the middle of its counties. Each route has a `path` of neighbouring cells
along the road links in infrastructure.json (Interstates preferred, a cross-country step where no road joins):
FAF says where freight starts and ends, not which way it went, so the path is a drawing aid, not data.
"""

from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["_world", "faf5", "census_boundaries", "cities.json", "infrastructure.json"]
PRODUCES = ["trade.json"]

TOP_N = 40
MIN_KM = 250
PARTNERS = 5
OFF_MAP = {"02", "15"}  # Alaska, Hawaii (state FIPS)
MODES = [("truck", "Truck"), ("rail", "Rail"), ("water", "Water"), ("air", "Air (incl. truck-air)"),
         ("multiple", "Multiple modes and mail"), ("pipeline", "Pipeline"), ("other", "Other and unknown")]
# Path costs per hex step: Interstate, major highway, rail-only, cross-country land, water.
STEP_COST = {"interstate": 1.0, "major": 1.4, "rail": 1.8, "land": 4.0, "water": 10.0}

RULE = (f"The {TOP_N} largest domestic freight flows by value between FAF zones in different states (no state in "
        f"common), counting only shipments that cross a state line, between zones whose anchors are at least {MIN_KM} "
        "km apart. The parts of one metro area "
        "split by a state line (e.g. New York-Newark in NY, NJ, CT and PA) are merged into one zone. Both directions "
        "are summed. Values are FAF model estimates for the year, in million current dollars and thousand short tons.")
PATH_NOTE = ("FAF gives a shipment's start and end zones, not its route: any line drawn between the two anchor "
             "cities is a drawing aid, not the way the goods travel.")


# ------------------------------------------------------------------ zones


def build_zones(faf: dict, world: dict, cities: list[dict], abbr_of: dict) -> tuple[dict, dict]:
    """Merged zones keyed by id, and FAF code → zone id (zones off the map are absent)."""
    grid = HexGrid(**world["grid"])
    parts = {k: z for k, z in faf["zones"].items() if z["state_fips"] not in OFF_MAP}
    groups = defaultdict(list)
    for k, z in sorted(parts.items()):
        groups[z["cfsma"] if z["cfsma"] != "99999" else f"rest-{k}"].append(k)

    geoids = world["counties"]
    state_counties = defaultdict(set)
    for g in geoids:
        state_counties[g[:2]].add(g)
    metro_counties = defaultdict(set)
    for z in parts.values():
        if z["cfsma"] != "99999":
            metro_counties[z["state_fips"]].update(z["counties"])

    zones, zone_of = {}, {}
    for key, codes in groups.items():
        members = [parts[c] for c in codes]
        rest = members[0]["cfsma"] == "99999"
        if rest:
            sf = members[0]["state_fips"]
            counties = sorted(state_counties[sf] - metro_counties[sf])
        else:
            counties = sorted({g for z in members for g in z["counties"]})
        zid = codes[0]
        lead = members[0]
        if rest:
            name = lead["name"].replace("Remainder of ", "Rest of ")
        else:
            name = lead.get("delineation_title") or lead["name"]
            if len(members) == 1 and lead.get("delineation_title"):
                name = lead["name"]  # the CFS name for a one-state zone
        zones[zid] = {"id": zid, "faf": codes, "name": name, "short": None,
                      "kind": "rest_of_state" if rest else "metro",
                      "states": sorted({abbr_of.get(z["state_fips"], z["state"]) for z in members}),
                      "cities": [], "_counties": set(counties), "_shorts": [z["short"] for z in members]}
        for c in codes:
            zone_of[c] = zid

    # Cities: each cities.json city joins the zone holding most of its member counties.
    county_zone = {g: zid for zid, z in zones.items() for g in z["_counties"]}
    for c in cities:  # population order
        votes = defaultdict(int)
        for g in c.get("counties") or []:
            if g in county_zone:
                votes[county_zone[g]] += 1
        if votes:
            zones[max(votes, key=lambda k: (votes[k], k))]["cities"].append(c["id"])

    by_id = {c["id"]: c for c in cities}
    owner, county = world["owner"], world["county"]
    cells_of = defaultdict(list)
    for i, ci in enumerate(county):
        if ci >= 0 and owner[i] >= 0 and geoids[ci] in county_zone:
            cells_of[county_zone[geoids[ci]]].append(i)
    for zid, z in zones.items():
        if z["cities"]:
            c = by_id[z["cities"][0]]
            z["anchor"] = {"cell": c["cell"], "x": c["x"], "y": c["y"], "city": c["id"]}
        else:
            cells = cells_of.get(zid) or []
            if cells:
                pts = [grid.center(*grid.col_row(i)) for i in cells]
                mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                cell = min(cells, key=lambda i: math.dist(grid.center(*grid.col_row(i)), (mx, my)))
                x, y = grid.center(*grid.col_row(cell))
                z["anchor"] = {"cell": cell, "x": round(x, 1), "y": round(y, 1), "city": None}
            else:
                z["anchor"] = None
        shorts = [s for s in z.pop("_shorts") if s]
        if z["kind"] == "metro":
            z["short"] = (by_id[z["cities"][0]]["name"] if z["cities"] else None) or (shorts[0] if shorts else None)
        else:
            z["short"] = z["name"]
        z["counties"] = len(z.pop("_counties"))
    return zones, zone_of


# ------------------------------------------------------------------ flows


def aggregate(faf: dict, parts_state: dict, zone_of: dict):
    """Route sums between merged zones (interstate part pairs only) and all partner sums (any part pair)."""
    route = defaultdict(lambda: {"modes": defaultdict(lambda: [0.0, 0.0, 0.0]), "sctg": defaultdict(float)})
    partner = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))  # zone → other → mode → value
    for p in faf["pairs"]:
        a, b = p["a"], p["b"]
        if a not in zone_of or b not in zone_of:
            continue
        za, zb = zone_of[a], zone_of[b]
        if za == zb:
            continue
        for m, (v, t, tm) in p["modes"].items():
            partner[za][zb][m] += v
            partner[zb][za][m] += v
        if parts_state[a] == parts_state[b]:
            continue
        key = (za, zb) if za < zb else (zb, za)
        r = route[key]
        for m, vals in p["modes"].items():
            acc = r["modes"][m]
            for i in range(3):
                acc[i] += vals[i]
        for code, v in p["commodities"]:
            r["sctg"][code] += v
    return route, partner


# ------------------------------------------------------------------ paths


def road_graph(infra: dict | None) -> dict:
    cost = {}
    if not infra:
        return cost
    classes = [c["id"] for c in infra.get("road_classes", [])]
    for a, b, *_ in infra.get("rail", []):
        cost[(a, b)] = STEP_COST["rail"]
    for a, b, cls, *_ in infra.get("roads", []):
        cost[(a, b)] = min(cost.get((a, b), 99), STEP_COST.get(classes[cls] if cls < len(classes) else "major", 1.4))
    return cost


def shortest_path(grid: HexGrid, owner: list, links: dict, src: int, dst: int) -> list[int]:
    """Cheapest chain of neighbouring cells from src to dst (Dijkstra with an A* bound)."""
    def h(c):
        x0, y0 = grid.center(*grid.col_row(c))
        x1, y1 = grid.center(*grid.col_row(dst))
        return math.hypot(x1 - x0, y1 - y0) / (math.sqrt(3) * grid.radius) * STEP_COST["interstate"]

    best = {src: 0.0}
    prev = {}
    heap = [(h(src), 0.0, src)]
    while heap:
        _, d, c = heapq.heappop(heap)
        if c == dst:
            break
        if d > best.get(c, math.inf):
            continue
        for n in grid.neighbours(c):
            key = (c, n) if c < n else (n, c)
            step = links.get(key)
            if step is None:
                step = STEP_COST["land"] if owner[n] >= 0 else STEP_COST["water"]
            nd = d + step
            if nd < best.get(n, math.inf):
                best[n] = nd
                prev[n] = c
                heapq.heappush(heap, (nd + h(n), nd, n))
    if dst not in best:
        return [src, dst] if src == dst else []
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    return path[::-1]


# ------------------------------------------------------------------ build


def build() -> None:
    world = load_interim("_world")
    grid, owner = HexGrid(**world["grid"]), world["owner"]
    payload = load_interim_payload("faf5")
    faf = payload["data"]
    bounds = load_interim("census_boundaries")["states"]
    abbr_of = {s["fips"]: s["abbr"] for s in bounds}
    cities = json.loads((WEB_DATA / "cities.json").read_text())
    ipath = WEB_DATA / "infrastructure.json"
    infra = json.loads(ipath.read_text()) if ipath.exists() else None

    zones, zone_of = build_zones(faf, world, cities, abbr_of)
    parts_state = {k: z["state_fips"] for k, z in faf["zones"].items()}
    route, partner = aggregate(faf, parts_state, zone_of)

    mode_ids = [m for m, _ in MODES]
    cands = []
    for (a, b), r in route.items():
        za, zb = zones[a], zones[b]
        if not za["anchor"] or not zb["anchor"] or set(za["states"]) & set(zb["states"]):
            continue  # zones sharing a state (New York-Newark vs the rest of New York) are not a trade route
        dist = math.dist((za["anchor"]["x"], za["anchor"]["y"]), (zb["anchor"]["x"], zb["anchor"]["y"]))
        if dist < MIN_KM:
            continue
        value = sum(v[0] for v in r["modes"].values())
        if value <= 0:
            continue
        cands.append((value, dist, a, b, r))
    cands.sort(key=lambda t: (-t[0], t[2], t[3]))

    links = road_graph(infra)
    routes, used_codes = [], set()
    for rank, (value, dist, a, b, r) in enumerate(cands[:TOP_N], 1):
        modes = r["modes"]
        shares = {m: round(modes[m][0] / value, 4) if m in modes else 0.0 for m in mode_ids}
        top_mode = max(mode_ids, key=lambda m: shares[m])
        top_c = max(r["sctg"].items(), key=lambda kv: kv[1])[0] if r["sctg"] else None
        if top_c:
            used_codes.add(top_c)
        path = shortest_path(grid, owner, links, zones[a]["anchor"]["cell"], zones[b]["anchor"]["cell"])
        routes.append({
            "a": a, "b": b, "rank": rank,
            "value_musd": round(value, 1),
            "tons_k": round(sum(v[1] for v in modes.values()), 1),
            "tmiles_m": round(sum(v[2] for v in modes.values()), 1),
            "modes": shares, "top_mode": top_mode, "top_commodity": top_c,
            "dist_km": round(dist),
            "path": path,
        })

    # Each zone's leading partners (any zone, either direction, all modes) and its totals.
    tot = faf["zone_totals"]
    internal = defaultdict(float)  # flows between the merged parts of one zone
    for p in faf["pairs"]:
        if p["a"] in zone_of and zone_of[p["a"]] == zone_of.get(p["b"]):
            internal[zone_of[p["a"]]] += sum(v[0] for v in p["modes"].values())
    for zid, z in zones.items():
        ps = []
        for other, ms in partner[zid].items():
            v = sum(ms.values())
            if v > 0:
                ps.append([other, round(v, 1), max(ms, key=ms.get)])
        ps.sort(key=lambda p: (-p[1], p[0]))
        z["partners"] = ps[:PARTNERS]
        within = sum(tot.get(code, {}).get("within", 0.0) for code in z["faf"]) + internal[zid]
        z["totals_musd"] = {
            "within": round(within, 1),                      # shipped inside the zone (incl. between merged parts)
            "other_zones": round(sum(sum(ms.values()) for ms in partner[zid].values()), 1),  # to + from other zones
            "import": round(sum(tot.get(c, {}).get("import", 0.0) for c in z["faf"]), 1),
            "export": round(sum(tot.get(c, {}).get("export", 0.0) for c in z["faf"]), 1),
        }

    nat = defaultdict(float)
    for code, t in tot.items():
        for k, v in t.items():
            nat[k] += v
    commodities = {k: v for k, v in faf["commodities"].items() if k in used_codes}

    used_zones = {r["a"] for r in routes} | {r["b"] for r in routes}
    doc = {
        "version": 1,
        "vintage": payload["vintage"],
        "year": faf["year"],
        "sources": ["faf5"],
        "rule": RULE,
        "path_note": PATH_NOTE,
        "units": {"value_musd": "million current US dollars", "tons_k": "thousand short tons",
                  "tmiles_m": "million ton-miles"},
        "modes": [{"id": m, "name": n} for m, n in MODES],
        "commodities": commodities,
        "national_musd": {"domestic": round(nat["within"] + nat["out"], 1), "import": round(nat["import"], 1),
                          "export": round(nat["export"], 1)},
        "zones": sorted(zones.values(), key=lambda z: z["id"]),
        "routes": routes,
    }
    write_web("trade.json", doc)
    print(f"    {len(zones)} zones ({sum(1 for z in zones.values() if len(z['faf']) > 1)} merged twins), "
          f"{len(cands)} interstate pairs ≥ {MIN_KM} km, {len(routes)} routes over {len(used_zones)} zones; "
          f"top: {zones[routes[0]['a']]['short']} – {zones[routes[0]['b']]['short']} ${routes[0]['value_musd'] / 1e3:.1f}B"
          if routes else "    no routes")


# ------------------------------------------------------------------ check


def check(web_dir: Path) -> list[tuple[str, str]]:
    path = Path(web_dir) / "trade.json"
    if not path.exists():
        return [("NOTE", "trade.json not built (optional; run: build trade --deps)")]
    out = []
    doc = json.loads(path.read_text())
    for k in ("version", "vintage", "year", "sources", "rule", "modes", "commodities", "zones", "routes"):
        if k not in doc:
            out.append(("FAIL", f"trade.json lacks '{k}'"))
    if out:
        return out
    if doc["version"] != 1:
        out.append(("FAIL", f"trade.json version {doc['version']!r} (expected 1)"))
    wpath = Path(web_dir) / "world.json"
    world = json.loads(wpath.read_text()) if wpath.exists() else None
    grid = HexGrid(world["grid"]["radius"], world["grid"]["cols"], world["grid"]["rows"]) if world and "grid" in world else None
    owner = (world or {}).get("owner") or []
    cpath = Path(web_dir) / "cities.json"
    city_ids = {c["id"] for c in json.loads(cpath.read_text())} if cpath.exists() else None

    zones = {}
    probs = []
    for z in doc["zones"]:
        for k in ("id", "faf", "name", "short", "kind", "states", "cities", "anchor"):
            if k not in z:
                probs.append(f"zone {z.get('id')} lacks {k}")
        zones[z.get("id")] = z
        if z.get("kind") not in ("metro", "rest_of_state"):
            probs.append(f"zone {z.get('id')} kind {z.get('kind')!r}")
        a = z.get("anchor")
        if a is not None and grid is not None:
            c = a.get("cell")
            if not isinstance(c, int) or not 0 <= c < grid.size or (owner and owner[c] < 0):
                probs.append(f"zone {z.get('id')} anchor cell {c!r} is not US land")
        if city_ids is not None and any(c not in city_ids for c in z.get("cities") or []):
            probs.append(f"zone {z.get('id')} lists unknown cities")
    if len(zones) != len(doc["zones"]):
        probs.append("duplicate zone ids")
    if probs:
        out.append(("FAIL", f"trade.json zones: {len(probs)} problems, e.g. {probs[:3]}"))
    else:
        out.append(("PASS", f"trade.json zones: {len(zones)} zones, {sum(1 for z in zones.values() if z['anchor'])} anchored"))

    mode_ids = {m["id"] for m in doc["modes"]}
    probs, ranks, pairs = [], [], set()
    for r in doc["routes"]:
        a, b = zones.get(r.get("a")), zones.get(r.get("b"))
        if a is None or b is None:
            probs.append(f"route {r.get('rank')}: unknown zone")
            continue
        pairs.add(tuple(sorted((r["a"], r["b"]))))
        ranks.append(r.get("rank"))
        for k in ("value_musd", "tons_k", "dist_km"):
            if not isinstance(r.get(k), (int, float)) or r[k] < 0:
                probs.append(f"route {r['rank']}: {k} {r.get(k)!r}")
        if not isinstance(r.get("value_musd"), (int, float)) or r["value_musd"] <= 0:
            probs.append(f"route {r['rank']}: value must be positive")
        if r.get("top_mode") not in mode_ids or set(r.get("modes", {})) - mode_ids:
            probs.append(f"route {r['rank']}: unknown mode")
        shares = list(r.get("modes", {}).values())
        if shares and abs(sum(shares) - 1) > 0.01:
            probs.append(f"route {r['rank']}: mode shares sum to {sum(shares):.3f}")
        if r.get("top_commodity") is not None and r["top_commodity"] not in doc["commodities"]:
            probs.append(f"route {r['rank']}: commodity {r['top_commodity']} not named")
        p = r.get("path")
        if p is not None and grid is not None:
            if not p or (a["anchor"] and p[0] != a["anchor"]["cell"]) or (b["anchor"] and p[-1] != b["anchor"]["cell"]):
                probs.append(f"route {r['rank']}: path does not join the anchors")
            elif any(v not in grid.neighbours(u) for u, v in zip(p, p[1:])):
                probs.append(f"route {r['rank']}: path steps are not neighbours")
    if ranks != list(range(1, len(ranks) + 1)):
        probs.append("ranks are not 1..N in order")
    vals = [r["value_musd"] for r in doc["routes"] if isinstance(r.get("value_musd"), (int, float))]
    if vals != sorted(vals, reverse=True):
        probs.append("routes are not sorted by value")
    if len(pairs) != len(doc["routes"]):
        probs.append("duplicate zone pairs")
    if probs:
        out.append(("FAIL", f"trade.json routes: {len(probs)} problems, e.g. {probs[:3]}"))
    elif doc["routes"]:
        top = doc["routes"][0]
        out.append(("PASS", f"trade.json routes: {len(doc['routes'])} routes, ${sum(vals) / 1e3:,.0f}B in all; "
                            f"#1 {zones[top['a']]['short']} – {zones[top['b']]['short']} ${top['value_musd'] / 1e3:,.1f}B"))
    else:
        out.append(("WARN", "trade.json has no routes"))
    size = path.stat().st_size
    if size > 300_000:
        out.append(("WARN", f"trade.json is {size / 1e3:.0f} kB (eager files should stay under ~300 kB)"))
    return out
