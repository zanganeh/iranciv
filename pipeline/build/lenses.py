"""Lenses: one value per hex for the map's colour overlays (see docs/DATA_CONTRACTS.md, lenses.json).

County values are spread to hexes in proportion to how much of each county the hex covers (its 7 county samples),
weighted by population. Province values (GDP per person) colour every hex of the province. Earthquakes count USGS
epicentres near each hex. Land lenses come from world.json's WorldCover shares.
"""

from __future__ import annotations

import math
from collections import Counter

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.store import load_interim, write_web
from .yields import HEX_AREA_KM2

REQUIRES = ["_world", "_economy", "wikidata_population", "usgs_quakes", "states.json"]
PRODUCES = ["lenses.json"]

GROUPS = [
    {"id": "people", "name": "People"},
    {"id": "prosperity", "name": "Prosperity"},
    {"id": "hazards", "name": "Hazards"},
    {"id": "land", "name": "Land"},
]
QUAKE_RADIUS_KM = 50.0
DOMAIN_NOTE = " Colours span the 2nd–98th percentile of hexes; values beyond are drawn at the end colours."


def nice(v: float, up: bool) -> float:
    if v == 0:
        return 0
    mag = 10 ** (math.floor(math.log10(abs(v))) - 1)
    return round((math.ceil if up else math.floor)(v / mag) * mag, 10)


def percentile_domain(values: list, log: bool) -> list[float] | None:
    vals = sorted(v for v in values if v is not None and (v > 0 or not log))
    if not vals:
        return None
    lo, hi = vals[int(0.02 * (len(vals) - 1))], vals[int(0.98 * (len(vals) - 1))]
    lo, hi = nice(lo, False), nice(hi, True)
    return [lo, hi] if hi > lo else [lo, nice(vals[-1], True)]


LAND_LENSES = {
    "forest": (["forest"], {
        "name": "Forest", "icon": "l-forest", "emoji": "🌲",
        "ramp": ["#0b1f0f", "#14401c", "#1f6b2b", "#3a9443", "#7fc07a", "#d5efcd"],
        "description": "Share of the hex's land covered by trees (ESA WorldCover 2021): the Hyrcanian forests on "
        "the Caspian slopes and the oak woodland of the Zagros.",
    }),
    "farmland": (["crop", "pasture"], {
        "name": "Farmland", "icon": "l-farmland", "emoji": "🚜",
        "ramp": ["#141d05", "#2f4510", "#557019", "#84a02a", "#b8cf55", "#eef7b8"],
        "description": "Share of the hex's land that is cropland (ESA WorldCover 2021). WorldCover has no "
        "separate pasture class; grazed steppe counts as grassland.",
    }),
    "developed": (["developed"], {
        "name": "Built-up", "icon": "l-developed", "emoji": "🏙️",
        "ramp": ["#121218", "#2c2c3c", "#4b4b66", "#727294", "#a3a3c2", "#e2e2f0"],
        "description": "Share of the hex's land that is built-up (ESA WorldCover 2021): buildings, roads and "
        "other sealed surfaces.",
    }),
}


def build() -> None:
    import json
    from ..core.paths import WEB_DATA

    world = load_interim("_world")
    eco = load_interim("_economy")
    quakes = load_interim("usgs_quakes")["quakes"]
    states = json.loads((WEB_DATA / "states.json").read_text())
    proj, grid = MapProjection(**world["projection"]), HexGrid(**world["grid"])
    owner, samples = world["owner"], world["county_samples"]
    n = grid.size

    density_c, growth_c = eco["county_density"], eco["county_growth"]
    hits = Counter(k for s in samples for k in s if k is not None and k >= 0)

    density = [None] * n
    growth = [None] * n
    for i, s in enumerate(samples):
        if owner[i] < 0 or not s:
            continue
        parts = Counter(k for k in s if k is not None and k >= 0)
        dens = [(density_c[k], h) for k, h in parts.items() if density_c[k] is not None]
        if dens:
            density[i] = round(sum(d * h for d, h in dens) / sum(h for _, h in dens), 1)
        # Growth: population-weighted over the counties in the hex.
        gs = [(growth_c[k], density_c[k] * h) for k, h in parts.items()
              if growth_c[k] is not None and density_c[k]]
        w = sum(p for _, p in gs)
        if w:
            growth[i] = round(sum(g * p for g, p in gs) / w, 4)

    gdp_pc = [None] * n
    from_province = 0
    for i, o in enumerate(owner):
        if o >= 0 and growth[i] is None and states[o]["growth"] is not None:
            growth[i] = states[o]["growth"]
            from_province += 1
        if o >= 0:
            s = states[o]
            gdp_pc[i] = round(s["gdp"] / s["population"]) if s["gdp"] and s["population"] else None

    # Earthquakes within QUAKE_RADIUS_KM of the hex centre.
    qxy = [(*proj(q["lon"], q["lat"]), q["mag"]) for q in quakes]
    count = [None] * n
    maxmag = [None] * n
    for i in range(n):
        if owner[i] < 0:
            continue
        cx, cy = grid.center(*grid.col_row(i))
        near = [m for x, y, m in qxy if abs(x - cx) <= QUAKE_RADIUS_KM and abs(y - cy) <= QUAKE_RADIUS_KM
                and math.hypot(x - cx, y - cy) <= QUAKE_RADIUS_KM]
        count[i] = len(near)
        maxmag[i] = max(near) if near else None

    lenses = [
        {
            "id": "density", "group": "people", "name": "Population", "icon": "l-people", "emoji": "👥",
            "unit": "people / km²", "format": "int", "scale": "log",
            "ramp": ["#1b1035", "#4b2377", "#9a3b8a", "#e0645e", "#f9b24a", "#fff3a8"], "higher_is": "neutral",
            "source": "wikidata_population",
            "description": "Residents per km² at the 2016 census: each county's population over its area, "
            "averaged over the counties the hex covers. Large rural counties look evenly spread even where people "
            "live in a few towns.",
            "values": density,
        },
        {
            "id": "growth", "group": "people", "name": "Growth 2006–2016", "icon": "l-growth", "emoji": "📈",
            "unit": "population change", "format": "pct", "scale": "linear",
            "ramp": ["#8e2a2a", "#c96a4a", "#e8c9a0", "#a9cf8a", "#4f9a4a", "#1f5f2a"], "higher_is": "good",
            "source": "wikidata_population",
            "description": "Population change between the 2006 and 2016 censuses, per county, weighted by population "
            "where a hex covers several counties.",
            "note": "Where the county has no 2006 figure in Wikidata (most were split or redrawn since), the "
            "hex shows its province's growth instead.",
            "values": growth,
        },
        {
            "id": "gdp_pc", "group": "prosperity", "name": "GDP per person", "icon": "l-income", "emoji": "💵",
            "unit": "USD per person (2016)", "format": "usd", "scale": "linear",
            "ramp": ["#2a1a05", "#5a3a0a", "#8f6414", "#c59a2a", "#e8c95a", "#fff1b0"], "higher_is": "good",
            "source": "province_gdp",
            "description": "The province's 2016 GDP (SCI share × World Bank national GDP) divided by its 2016 "
            "population. Oil-producing provinces (Khuzestan, Bushehr, Kohgiluyeh and Boyer-Ahmad) rank high because "
            "oil and gas output is counted where it is extracted.",
            "note": "Province-wide values: every hex of a province has the same colour.",
            "values": gdp_pc,
        },
        {
            "id": "quakes", "group": "hazards", "name": "Earthquakes", "icon": "l-disasters", "emoji": "🌋",
            "unit": "quakes of M5.5+ since 1900", "format": "int", "scale": "linear",
            "ramp": ["#1a1414", "#3d2420", "#6e3326", "#a8452a", "#dc6a2e", "#ffb35a"], "higher_is": "bad",
            "source": "usgs_quakes",
            "description": f"Earthquakes of magnitude 5.5 or more since 1900 (USGS catalogue) with an epicentre within "
            f"{QUAKE_RADIUS_KM:.0f} km of the hex centre.",
            "note": "Early-20th-century records are incomplete; zero means none recorded, not no risk.",
            "values": count,
        },
        {
            "id": "quake_max", "group": "hazards", "name": "Strongest quake", "icon": "l-disasters", "emoji": "📉",
            "unit": "magnitude", "format": "float", "scale": "linear", "domain": [5.5, 7.5],
            "ramp": ["#2a2018", "#5a3a20", "#8f5226", "#c2692a", "#e8873a", "#ffc06a"], "higher_is": "bad",
            "source": "usgs_quakes",
            "description": f"The largest magnitude among M5.5+ earthquakes since 1900 within {QUAKE_RADIUS_KM:.0f} km "
            "of the hex centre (USGS catalogue). Blank where none was recorded.",
            "values": maxmag,
        },
    ]
    pct, cover = world["pct"], world["cover"]
    for lid, (keys, meta) in LAND_LENSES.items():
        values = [round(sum(pct[k][i] for k in keys) / 100, 2) if owner[i] >= 0 and cover[i] else None
                  for i in range(n)]
        lenses.append({"id": lid, "group": "land", **meta, "unit": "share of land", "format": "pct",
                       "scale": "linear", "higher_is": "neutral", "source": "worldcover", "values": values})

    for lens in lenses:
        if "domain" in lens:
            continue
        lens["domain"] = percentile_domain(lens["values"], lens["scale"] == "log")
        lens["description"] += DOMAIN_NOTE
    groups = [g for g in GROUPS if any(l["group"] == g["id"] for l in lenses)]
    write_web("lenses.json", {"vintage": "Census 2016", "groups": groups, "lenses": lenses})
    print(f"    growth: {from_province} hexes use their province's rate")
    print(f"    {len(lenses)} lenses; hex area {HEX_AREA_KM2:.0f} km²")
