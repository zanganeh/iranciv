"""Cities, provinces (states.json), counties and the nation: population, growth, yields and ranks.

Population is the 2016 census (via Wikidata), growth is 2006 → 2016. Gold is provincial GDP (SCI shares × World Bank
national GDP, 2016); counties and cities get their province's GDP split by population (an estimate, said so in the
yield description). The other yields are mapped quantities summed over hexes (see yields.py).

A city is placed at its true position and gets one land cell for its banner (snapped to the nearest Iranian land cell,
nudged up to 2 rings when a bigger city holds it). Its footprint is the hexes of its county (shahrestan), the closest
Iranian match to a US metro area, when it is the largest city of that county; otherwise only its own cell.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.region import CAPITAL
from ..core.store import load_interim, write_web
from .labels import label_anchor
from .yields import HEX_AREA_KM2, YIELD_KEYS, YIELDS

REQUIRES = ["_world", "iran_boundaries", "wikidata_population", "province_gdp", "wikidata_sites",
            "wri_power_plants"]
PRODUCES = ["cities.json", "states.json", "nation.json", "counties.json", "_economy"]

POP_YEAR, BASE_YEAR = 2016, 2006
PEERS = 5
PCT_KEYS = ["forest", "crop", "pasture", "grass", "shrub", "wetland", "developed"]
CAPITAL_FIX = {"Bandar Abbas": "Q633659"}  # Wikidata lacks the P1376 claim
EXCLUDED_FUELS = {"Nuclear"}
WHS_MERGE_KM = 40.0


def ring_search(grid, start, max_rings):
    seen, frontier = {start}, [start]
    yield 0, start
    for ring in range(1, max_rings + 1):
        nxt = []
        for c in frontier:
            for n in grid.neighbours(c):
                if n not in seen:
                    seen.add(n)
                    nxt.append(n)
                    yield ring, n
        frontier = nxt


def land_cell(grid, owner, x, y, taken=None, max_rings=6):
    """The Iranian land cell at (x, y), else the nearest one (not in `taken`, when given)."""
    start = grid.cell_at(x, y)
    if start < 0:
        return -1
    best = None
    for ring, c in ring_search(grid, start, max_rings):
        if best is not None and ring > best[0] + 1:
            break
        if owner[c] < 0 or (taken is not None and c in taken):
            continue
        cx, cy = grid.center(*grid.col_row(c))
        score = (ring if best is None else best[0], math.hypot(cx - x, cy - y))
        if best is None or score[1] < best[1][1]:
            best = (ring if best is None else best[0], score, c)
    return best[2] if best else -1


def growth(a, b):
    if not a or not b:
        return None
    g = b / a - 1
    return round(g, 4) if abs(g) <= 0.6 else None


def shoelace_km2(rings, proj):
    total = 0.0
    for ring in rings:
        pts = [proj(lon, lat) for lon, lat in ring]
        s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
        total += s / 2
    return abs(total)


def whs_properties(whs):
    """Group component items into properties: same inscription year, within WHS_MERGE_KM of the group's first item."""
    groups = []
    for s in sorted(whs, key=lambda s: (s["year"] or 0, s["qid"])):
        if s["lon"] is None:
            continue
        for g in groups:
            f = g[0]
            d = math.hypot((s["lon"] - f["lon"]) * 111 * math.cos(math.radians(f["lat"])), (s["lat"] - f["lat"]) * 111)
            if f["year"] == s["year"] and d <= WHS_MERGE_KM:
                g.append(s)
                break
        else:
            groups.append([s])
    return groups


def build() -> None:
    world = load_interim("_world")
    bounds = load_interim("iran_boundaries")
    popd = load_interim("wikidata_population")
    gdpd = load_interim("province_gdp")
    sites = load_interim("wikidata_sites")
    plants = load_interim("wri_power_plants")["plants"]
    proj, grid = MapProjection(**world["projection"]), HexGrid(**world["grid"])
    owner, cover, pct = world["owner"], world["cover"], world["pct"]
    cell_county = world["county"] if "county" in world else None
    if cell_county is None:  # majority of the 7 samples
        cell_county = []
        for s in world["county_samples"]:
            cell_county.append(max(set(s), key=s.count) if s else -1)

    # ---- per-cell yield quantities ------------------------------------------------------------------------------
    n = grid.size
    cell_y = {k: [0.0] * n for k in YIELD_KEYS if k != "gold"}
    for i in range(n):
        if owner[i] >= 0 and cover[i]:
            cell_y["food"][i] = pct["crop"][i] / 100 * HEX_AREA_KM2

    def put(key, lon, lat, v=1.0):
        if lon is None or lat is None:
            return None
        c = land_cell(grid, owner, *proj(lon, lat), max_rings=2)
        if c >= 0:
            cell_y[key][c] += v
        return c

    for p in plants:
        if p["fuel"] not in EXCLUDED_FUELS and p["mw"]:
            put("production", p["lon"], p["lat"], p["mw"])
    for u in sites["universities"]:
        put("science", u["lon"], u["lat"])
    whs_groups = whs_properties(sites["whs"])
    for g in whs_groups:
        put("culture", g[0]["lon"], g[0]["lat"])

    def sum_cells(cells):
        return {k: round(sum(cell_y[k][c] for c in cells), 1) for k in cell_y}

    # ---- provinces -----------------------------------------------------------------------------------------------
    meta = {s["fips"]: s for s in bounds["states"]}
    by_name = {}
    nat_gdp = gdpd["national_gdp_usd"][str(gdpd["year"])]
    states = []
    cells_of_state = defaultdict(list)
    for i, o in enumerate(owner):
        if o >= 0:
            cells_of_state[o].append(i)
    for si, fips in enumerate(world["states"]):
        m = meta[fips]
        p = {int(y): v for y, v in popd["provinces"].get(m["wikidata"], {}).get("pop", {}).items()}
        share = gdpd["share_pct"].get(m["name"])
        gdp = round(nat_gdp * share / 100) if share is not None else None
        cells = cells_of_state[si]
        land_cells = [c for c in cells if cover[c]]
        ys = sum_cells(cells)
        st = {
            "fips": fips, "abbr": m["abbr"], "name": m["name"], "name_fa": m.get("name_fa"),
            "wikidata": m["wikidata"],
            "population": p.get(POP_YEAR), "employed": None,
            "yields": {**{k: ys.get(k) for k in YIELD_KEYS if k != "gold"}, "gold": gdp},
            "jobs": {}, "median_income": None, "median_rent": None, "home_value": None,
            "unemployment": None, "rent_burden": None,
            "gdp": gdp, "gdp_year": gdpd["year"], "gdp_share": share / 100 if share is not None else None,
            "growth": growth(p.get(BASE_YEAR), p.get(POP_YEAR)), "growth_years": [BASE_YEAR, POP_YEAR],
            "pop_series": {str(y): v for y, v in sorted(p.items())},
            "ranks": {},
            "label": label_anchor(grid, owner, si, m["name"]),
            "land": ({k: round(sum(pct[k][c] for c in land_cells) / len(land_cells) / 100, 3) for k in PCT_KEYS}
                     if land_cells else None),
            "energy": None,
            "land_km2": round(len(cells) * HEX_AREA_KM2),
        }
        states.append(st)
        by_name[m["name"]] = si
    missing = [s["name"] for s in states if s["population"] is None or s["gdp"] is None]
    if missing:
        raise SystemExit(f"provinces without population or GDP: {missing}")

    # ---- counties --------------------------------------------------------------------------------------------------
    cmeta = {c["geoid"]: c for c in bounds["counties"]}
    geoids = world["counties"]
    cells_of_county = defaultdict(list)
    for i, k in enumerate(cell_county):
        if k is not None and k >= 0 and owner[i] >= 0:
            cells_of_county[k].append(i)
    county_rows = []
    county_pop = {}
    for k, g in enumerate(geoids):
        m = cmeta[g]
        cp = popd["counties"].get(m["wikidata"] or "", {})
        county_pop[g] = cp.get("pop2016")
    prov_known = defaultdict(int)
    for g, v in county_pop.items():
        if v:
            prov_known[cmeta[g]["state_fips"]] += v
    fips_index = {f: i for i, f in enumerate(world["states"])}
    for k, g in enumerate(geoids):
        m = cmeta[g]
        si = fips_index[m["state_fips"]]
        st = states[si]
        area = shoelace_km2(m["rings"], proj)
        pop = county_pop[g]
        cp = popd["counties"].get(m["wikidata"] or "", {})
        cells = cells_of_county[k]
        land_cells = [c for c in cells if cover[c]]
        rep = None
        if not cells:
            xs = [proj(lon, lat) for ring in m["rings"] for lon, lat in ring]
            if xs:
                cx, cy = sum(x for x, _ in xs) / len(xs), sum(y for _, y in xs) / len(xs)
                rep = land_cell(grid, owner, cx, cy)
        county_rows.append({
            "geoid": g, "name": m["name"], "name_fa": m.get("name_fa"), "state": st["abbr"], "si": si,
            "population": pop, "pop2006": cp.get("pop2006"), "land_km2": round(area, 1),
            "density": round(pop / area, 1) if pop and area else None,
            "growth": growth(cp.get("pop2006"), pop),
            # Province GDP split by the county's share of the province population (estimate).
            "gdp": round(st["gdp"] * pop / st["population"]) if pop else None,
            "land": {kk: (round(sum(pct[kk][c] for c in land_cells) / len(land_cells) / 100, 4) if land_cells else None)
                     for kk in PCT_KEYS},
            "cells": cells, "cell": cells[len(cells) // 2] if cells else rep,
            "yields": sum_cells(cells),
        })

    # ---- cities --------------------------------------------------------------------------------------------------
    raw_cities = sorted(popd["cities"], key=lambda c: -c["pop"])
    taken = set()
    cities = []
    county_owner = {}  # county index → id of its largest city
    capital_by_prov = {}
    for c in raw_cities:
        x, y = proj(c["lon"], c["lat"])
        cell = land_cell(grid, owner, x, y)
        if cell < 0:
            continue
        free = land_cell(grid, owner, x, y, taken=taken, max_rings=2)
        if free >= 0:
            cell = free
        taken.add(cell)
        si = owner[cell]
        cap_of = CAPITAL_FIX.get(c["name"], c["capital_of"])
        k = cell_county[grid.cell_at(x, y)] if owner[grid.cell_at(x, y)] >= 0 else cell_county[cell]
        if k is None or k < 0:
            k = cell_county[cell]
        cid = c["qid"]
        if k >= 0 and k not in county_owner:
            county_owner[k] = cid
            fp = sorted(set(cells_of_county[k]) | {cell})
        else:
            fp = [cell]
        st = states[si]
        gdp = round(st["gdp"] * c["pop"] / st["population"])
        ys = sum_cells(fp)
        is_prov_cap = bool(cap_of) and cap_of == st["wikidata"]
        if is_prov_cap:
            capital_by_prov[si] = cid
        cities.append({
            "id": cid, "name": c["name"], "full": f"{c['name']}, {st['name']} Province",
            "states": [st["abbr"]], "metro": c["pop"] >= 100000, "capital": c["name"] == CAPITAL,
            "province_capital": is_prov_cap,
            "x": round(x, 1), "y": round(y, 1), "cell": cell,
            "population": c["pop"], "population_year": c["pop_year"], "employed": None,
            "yields": {**{kk: ys[kk] for kk in ys}, "gold": gdp},
            "jobs": {}, "median_income": None, "median_rent": None, "home_value": None,
            "unemployment": None, "rent_burden": None,
            "gdp": gdp, "gdp_year": gdpd["year"], "gdp_by_industry": None, "gdp_undisclosed": None,
            "gdp_estimated": None,
            "growth": growth(c["pop2006"], c["pop"]) if c["pop_year"] == POP_YEAR else None,
            "growth_years": [BASE_YEAR, POP_YEAR],
            "counties": [geoids[k]] if k >= 0 else [],
            "footprint": fp,
            "pop_series": ({str(BASE_YEAR): c["pop2006"], str(POP_YEAR): c["pop"]}
                           if c["pop_year"] == POP_YEAR else None),
            "ranks": {}, "peers": [],
            "wikidata": cid,
        })
    no_cap = [states[i]["name"] for i in range(len(states)) if i not in capital_by_prov]
    if no_cap:
        print(f"    provinces without a capital among the cities: {no_cap}")

    # ranks & peers
    rank_keys = ["population", "gdp", "gdp_per_capita", "growth"] + YIELD_KEYS

    def value(c, key):
        if key == "gdp_per_capita":
            return c["gdp"] / c["population"] if c["gdp"] and c["population"] else None
        if key in YIELD_KEYS:
            return c["yields"][key]
        return c[key]

    for key in rank_keys:
        ranked = sorted((c for c in cities if value(c, key) is not None),
                        key=lambda c: (-value(c, key), -c["population"]))
        for r, c in enumerate(ranked, 1):
            c["ranks"][key] = r
        for c in cities:
            c["ranks"].setdefault(key, None)
    for c in cities:
        others = sorted((o for o in cities if o is not c),
                        key=lambda o: abs(math.log(o["population"] / c["population"])))
        c["peers"] = [o["id"] for o in others[:PEERS]]
    city_of_county = {geoids[k]: cid for k, cid in county_owner.items()}

    for key in YIELD_KEYS:
        ranked = sorted((s for s in states if s["yields"][key] is not None), key=lambda s: -s["yields"][key])
        for r, s in enumerate(ranked, 1):
            s["ranks"][key] = r
        for s in states:
            s["ranks"].setdefault(key, None)

    # ---- nation --------------------------------------------------------------------------------------------------
    nat_pop = sum(s["population"] for s in states)
    nat_pop0 = sum(s["pop_series"].get(str(BASE_YEAR)) or 0 for s in states)
    nation = {
        "vintage": f"Census {POP_YEAR}", "year": POP_YEAR,
        "gdp_vintage": f"SCI provincial accounts {gdpd['year']} × World Bank GDP {gdpd['year']}",
        "nation": {
            "population": nat_pop, "employed": None,
            "yields": {k: (round(sum(s["yields"][k] for s in states), 1) if k != "gold" else nat_gdp)
                       for k in YIELD_KEYS},
            "jobs": {}, "median_income": None, "median_rent": None, "home_value": None,
            "unemployment": None, "rent_burden": None,
            "gdp": nat_gdp, "gdp_year": gdpd["year"], "gdp_by_industry": None, "gdp_undisclosed": None,
            "gdp_estimated": None, "gdp_extra_regional_share": gdpd["extra_regional_pct"] / 100,
            "growth": growth(nat_pop0, nat_pop), "growth_years": [BASE_YEAR, POP_YEAR],
        },
        "yields": YIELDS,
        "industries": {},
        "country": "Iran",
    }

    # ---- counties.json -------------------------------------------------------------------------------------------
    fields = [
        ("geoid", "County code", "text", None, "neutral", "iran_boundaries",
         "Province ISO code + a 3-digit index of the county within its province (by English name). Not an official code."),
        ("name", "County", "text", None, "neutral", "iran_boundaries", "OpenStreetMap English name."),
        ("name_fa", "Persian name", "text", None, "neutral", "iran_boundaries", "OpenStreetMap Persian name."),
        ("state", "Province", "text", None, "neutral", "iran_boundaries", "Province abbreviation."),
        ("population", "Population", "int", "people", "neutral", "wikidata_population", "2016 census."),
        ("land_km2", "Area", "km2", "km²", "neutral", "iran_boundaries",
         "Area of the OpenStreetMap county outline (equal-area projection)."),
        ("density", "Density", "float", "people / km²", "neutral", "wikidata_population",
         "2016 population ÷ area."),
        ("growth", "Growth 2006–2016", "pct", None, "good", "wikidata_population",
         "Population change between the 2006 and 2016 censuses. Null where the county was split or created in "
         "between, or Wikidata has no 2006 figure."),
        ("gdp", "GDP (estimate)", "usd", "USD", "good", "province_gdp",
         "The province's 2016 GDP split by the county's share of its population."),
        ("forest", "Forest", "pct", None, "neutral", "worldcover", "Share of land that is tree cover."),
        ("crop", "Cropland", "pct", None, "neutral", "worldcover", "Share of land that is cropland."),
        ("pasture", "Pasture", "pct", None, "neutral", "worldcover", "Share of land that is pasture."),
        ("developed", "Built-up", "pct", None, "neutral", "worldcover", "Share of land that is built-up."),
        ("cbsa", "City", "text", None, "neutral", "wikidata_population",
         "The id of the largest city in the county, when it is in cities.json."),
        ("cell", "Map cell", "int", None, "neutral", "iran_boundaries", "A representative land hex of the county."),
    ]
    columns = [f[0] for f in fields]
    rows = []
    for r in county_rows:
        rec = {**r, **{k: r["land"][k] for k in ("forest", "crop", "pasture", "developed")},
               "cbsa": city_of_county.get(r["geoid"])}
        rows.append([rec.get(c) for c in columns])
    counties_doc = {
        "vintage": f"Census {POP_YEAR}",
        "fields": [{"key": k, "label": lab, "format": fmt, "unit": unit, "higher_is": hi, "source": src,
                    "description": desc} for k, lab, fmt, unit, hi, src, desc in fields],
        "columns": columns, "rows": rows,
    }

    write_web("cities.json", cities)
    write_web("states.json", states)
    write_web("nation.json", nation)
    write_web("counties.json", counties_doc)
    from ..core.store import save_interim
    save_interim("_economy", {
        "county_density": [r["density"] for r in county_rows],
        "county_growth": [r["growth"] for r in county_rows],
        "cell_county": cell_county,
        "whs": [[s["name"] for s in g] for g in whs_groups],
    })
    print(f"    {len(cities)} cities ({sum(c['province_capital'] for c in cities)} province capitals), "
          f"{len(states)} provinces, {len(county_rows)} counties "
          f"({sum(1 for r in county_rows if r['population'])} with population); "
          f"population {nat_pop:,}, GDP ${nat_gdp / 1e9:,.1f}B; {len(whs_groups)} World Heritage properties")
