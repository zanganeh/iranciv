"""Lenses = per-hex map overlays built from county data (and land cover).

Each US hex is sampled at 7 points (see world.py). A county's population is
spread over the hexes in proportion to how many of all its sample points fall
in each hex, so every county's people are counted exactly once. Small counties
that no sample point hits (e.g. Manhattan, Virginia's independent cities) are
added in full to the hex holding their centre, or to the nearest US land hex if
that centre lies on water. Density = people ÷ the hex's land area; every other
county lens is the mean of the counties in the hex, weighted by those people.
"""

from __future__ import annotations

import math
from collections import Counter

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.store import load_interim, load_interim_payload, write_web
from .cities import nearest_land
from .disasters import county_declarations

REQUIRES = ["_world", "census_gazetteer", "acs_5yr", "usrc_2020", "cdc_places", "fema_declarations"]
PRODUCES = ["lenses.json"]

GROUPS = [
    {"id": "people", "name": "People"},
    {"id": "prosperity", "name": "Prosperity"},
    {"id": "knowledge", "name": "Knowledge"},
    {"id": "life", "name": "Daily life"},
    {"id": "faith", "name": "Faith"},
    {"id": "hazards", "name": "Hazards"},
    {"id": "land", "name": "Land"},
]


def ratio(rec: dict, num: list[str], den: str) -> float | None:
    """sum(rec[num]) / rec[den], or None if anything is missing or the denominator is 0."""
    d = rec.get(den)
    vals = [rec.get(k) for k in num]
    if not d or any(v is None for v in vals):
        return None
    return sum(vals) / d


def nice(v: float, up: bool) -> float:
    """v rounded outward (up or down) to 2 significant digits."""
    if v == 0:
        return 0
    mag = 10 ** (math.floor(math.log10(abs(v))) - 1)
    return round((math.ceil if up else math.floor)(v / mag) * mag, 10)


def percentile_domain(values: list, log: bool) -> list[float] | None:
    """[2nd, 98th] percentile of the non-null hex values, rounded outward; positive values only on a log scale."""
    vals = sorted(v for v in values if v is not None and (v > 0 or not log))
    if not vals:
        return None
    lo, hi = vals[int(0.02 * (len(vals) - 1))], vals[int(0.98 * (len(vals) - 1))]
    lo, hi = nice(lo, False), nice(hi, True)
    return [lo, hi] if hi > lo else [lo, nice(vals[-1], True)]


DOMAIN_NOTE = " Colours span the 2nd–98th percentile of hexes; values beyond are drawn at the end colours."


# County lenses: id → (how to compute the county value from ACS 5-year, lens metadata).
COUNTY_LENSES = {
    "income": (
        lambda r: r.get("B19013_001"),
        {
            "group": "prosperity", "name": "Income", "icon": "l-income", "emoji": "💵",
            "unit": "median household income, $", "format": "usd", "scale": "linear",
            "higher_is": "good",
            "ramp": ["#3a1c0b", "#7a4413", "#b8781f", "#e3b23c", "#f6e27f", "#fffbe0"],
            "description": "Median household income of the counties in each hex, weighted by population "
            "(ACS B19013, inflation-adjusted to the last survey year).",
        },
    ),
    "home": (
        lambda r: r.get("B25077_001"),
        {
            "group": "prosperity", "name": "Home value", "icon": "l-home", "emoji": "🏠",
            "unit": "median home value, $", "format": "usd", "scale": "log",
            "higher_is": "neutral",
            "ramp": ["#0b2a2e", "#165a5c", "#2f8f83", "#7cc29a", "#d4ecb0", "#ffffe0"],
            "description": "Median value of owner-occupied homes, weighted by population (ACS B25077).",
        },
    ),
    "poverty": (
        lambda r: ratio(r, ["B17001_002"], "B17001_001"),
        {
            "group": "prosperity", "name": "Poverty", "icon": "l-poverty", "emoji": "🥣",
            "unit": "share of people below the poverty line", "format": "pct", "scale": "linear",
            "higher_is": "bad",
            "ramp": ["#03051a", "#4c1d4b", "#a11a5b", "#e83f3f", "#f69c73", "#faebdd"],
            "description": "Share of people whose family income in the past 12 months was below the official "
            "poverty threshold (ACS B17001 line 2 ÷ line 1; universe: people for whom poverty status is "
            "determined, which excludes e.g. people in dorms and institutions).",
        },
    ),
    "age": (
        lambda r: r.get("B01002_001"),
        {
            "group": "people", "name": "Median age", "icon": "l-age", "emoji": "🎂",
            "unit": "years", "format": "years", "scale": "linear",
            "higher_is": "neutral",
            "ramp": ["#00224e", "#35456c", "#666970", "#948e77", "#c8b866", "#fee838"],
            "description": "Median age of residents (ACS B01002). A hex mixing several counties shows the "
            "population-weighted mean of their medians, an approximation of the true median.",
        },
    ),
    "foreign_born": (
        lambda r: ratio(r, ["B05012_003"], "B05012_001"),
        {
            "group": "people", "name": "Born abroad", "icon": "l-foreign-born", "emoji": "🌍",
            "unit": "share of residents born outside the U.S.", "format": "pct", "scale": "linear",
            "higher_is": "neutral",
            "ramp": ["#0b0405", "#382a54", "#395d9c", "#3497a9", "#60ceac", "#def5e5"],
            "description": "Share of residents who are foreign-born (ACS B05012 line 3 ÷ line 1).",
        },
    ),
    "bachelors": (
        lambda r: ratio(r, ["B15003_022", "B15003_023", "B15003_024", "B15003_025"], "B15003_001"),
        {
            "group": "knowledge", "name": "Degrees", "icon": "l-degrees", "emoji": "🎓",
            "unit": "adults 25+ with a bachelor's degree or higher", "format": "pct", "scale": "linear",
            "higher_is": "good",
            "ramp": ["#08203e", "#0f4c81", "#2a7fc1", "#5fa8e0", "#a6d1f2", "#eaf5ff"],
            "description": "Share of adults aged 25 and over whose highest degree is a bachelor's, master's, "
            "professional or doctorate (ACS B15003 lines 22–25 ÷ line 1).",
        },
    ),
    "broadband": (
        lambda r: ratio(r, ["B28002_004"], "B28002_001"),
        {
            "group": "knowledge", "name": "Broadband", "icon": "l-broadband", "emoji": "📡",
            "unit": "households with a broadband subscription", "format": "pct", "scale": "linear",
            "higher_is": "good",
            "ramp": ["#1a0a2e", "#3f1d6b", "#6a3fa0", "#9a72c9", "#c9a9e6", "#f3e8ff"],
            "description": "Share of households with a broadband internet subscription of any type, "
            "including cellular data plans (ACS B28002 line 4 ÷ line 1).",
        },
    ),
    "commute": (
        lambda r: ratio(r, ["B08013_001"], "B08303_001"),
        {
            "group": "life", "name": "Commute", "icon": "l-commute", "emoji": "🚗",
            "unit": "mean minutes to work, one way", "format": "minutes", "scale": "linear",
            "higher_is": "bad",
            "ramp": ["#2b0b1e", "#5e1742", "#93275f", "#c84f7c", "#ea8fa8", "#fbd9e1"],
            "description": "Mean one-way travel time to work of workers 16+ who do not work from home "
            "(ACS B08013 aggregate minutes ÷ B08303 workers).",
        },
    ),
    "homeownership": (
        lambda r: ratio(r, ["B25003_002"], "B25003_001"),
        {
            "group": "life", "name": "Homeowners", "icon": "l-homeownership", "emoji": "🔑",
            "unit": "occupied homes lived in by their owner", "format": "pct", "scale": "linear",
            "higher_is": "neutral",
            "ramp": ["#1c1a05", "#45410f", "#76701d", "#a8a033", "#d4ce62", "#f8f6c4"],
            "description": "Homeownership rate: owner-occupied ÷ all occupied housing units (ACS B25003).",
        },
    ),
    "uninsured": (
        lambda r: ratio(r, ["B27010_017", "B27010_033", "B27010_050", "B27010_066"], "B27010_001"),
        {
            "group": "life", "name": "Uninsured", "icon": "l-uninsured", "emoji": "🩺",
            "unit": "share of people without health insurance", "format": "pct", "scale": "linear",
            "higher_is": "bad",
            "ramp": ["#1a0505", "#4a0f0f", "#80201a", "#b8452b", "#e07b54", "#f9c9a8"],
            "description": "Share of the civilian noninstitutionalized population with no health insurance "
            "coverage (ACS B27010, the four 'no coverage' lines across age groups ÷ line 1).",
        },
    ),
}

# Land lenses from the world builder's per-hex land-cover shares (NLCD), 0..100 → 0..1.
LAND_LENSES = {
    "forest": (
        ["forest"],
        {
            "name": "Forest", "icon": "l-forest", "emoji": "🌲", 
            "ramp": ["#0b1f0f", "#14401c", "#1f6b2b", "#3a9443", "#7fc07a", "#d5efcd"],
            "description": "Share of the hex's land covered by deciduous, evergreen or mixed forest (NLCD).",
        },
    ),
    "farmland": (
        ["crop", "pasture"],
        {
            "name": "Farmland", "icon": "l-farmland", "emoji": "🚜", 
            "ramp": ["#141d05", "#2f4510", "#557019", "#84a02a", "#b8cf55", "#eef7b8"],
            "description": "Share of the hex's land in cultivated crops or pasture/hay (NLCD 81 + 82).",
        },
    ),
    "developed": (
        ["developed"],
        {
            "name": "Built-up", "icon": "l-developed", "emoji": "🏙️", 
            "ramp": ["#121218", "#2c2c3c", "#4b4b66", "#727294", "#a3a3c2", "#e2e2f0"],
            "description": "Share of the hex's land that is developed at low, medium or high intensity "
            "(NLCD 22–24; parks and lawns, NLCD 21, are not counted).",
        },
    ),
}


def build() -> None:
    world = load_interim("_world")
    gaz = load_interim("census_gazetteer")
    acs_payload = load_interim_payload("acs_5yr")
    acs = acs_payload["data"]["geos"]
    proj = MapProjection(**world["projection"])
    grid = HexGrid(**world["grid"])
    owner = world["owner"]
    samples = world["county_samples"]
    hex_area = 1.5 * math.sqrt(3) * grid.radius**2
    state_index = {fips: i for i, fips in enumerate(world["states"])}

    total_hits = Counter(k for s in samples for k in s if k >= 0)

    county = []  # aligned with world["counties"]
    for geoid in world["counties"]:
        rec = acs.get("0500000US" + geoid, {})
        pop = rec.get("B01003_001")
        county.append(
            {"geoid": geoid, "pop": pop, **{lid: f(rec) for lid, (f, _) in COUNTY_LENSES.items()}}
            if pop is not None
            else None
        )

    # Unsampled counties go to the hex holding their centre (moved onto US land if needed).
    small_by_cell: dict[int, list[int]] = {}
    moved, lost = [], []
    for k, c in enumerate(county):
        if not c or total_hits[k]:
            continue
        g = gaz["counties"].get(c["geoid"])
        if not g:
            lost.append(c["geoid"])
            continue
        x, y = proj(g["lon"], g["lat"])
        cell = grid.cell_at(x, y)
        if cell < 0 or not samples[cell]:
            cell = nearest_land(grid, owner, max(cell, 0), x, y, state_index.get(c["geoid"][:2], -1))
            moved.append(g["name"])
        if cell < 0:
            lost.append(c["geoid"])
            continue
        small_by_cell.setdefault(cell, []).append(k)

    people = [None] * grid.size
    weights: list[list[tuple[int, float]]] = [[] for _ in range(grid.size)]
    density = [None] * grid.size
    for i, s in enumerate(samples):
        if not s:
            continue
        parts = [(k, county[k]["pop"] * hits / total_hits[k]) for k, hits in Counter(x for x in s if x >= 0).items() if county[k]]
        parts += [(k, county[k]["pop"]) for k in small_by_cell.get(i, [])]
        land = sum(1 for x in s if x >= 0) or 1  # a hex whose samples all miss counties still counts its centre
        weights[i] = parts
        if parts:
            people[i] = sum(p for _, p in parts)
            density[i] = round(people[i] / (hex_area * land / len(s)), 2)

    total_counties = sum(c["pop"] for c in county if c)
    total_hexes = sum(p for p in people if p)
    print(f"    population on hexes {total_hexes:,.0f} of {total_counties:,.0f} ({total_hexes / total_counties:.4%})")
    if moved:
        print(f"    {len(moved)} unsampled counties moved off water: {', '.join(moved)}")
    if lost:
        print(f"    {len(lost)} counties not placed: {', '.join(lost)}")

    lenses = [
        {
            "id": "density", "group": "people", "name": "Population", "icon": "l-people", "emoji": "👥",
            "unit": "people / km²", "format": "int", "scale": "log", 
            "ramp": ["#1b1035", "#4b2377", "#9a3b8a", "#e0645e", "#f9b24a", "#fff3a8"], "higher_is": "neutral",
            "source": "acs_5yr",
            "description": "Residents per km² of land. Each county's population (ACS B01003) is spread over its "
            "hexes in proportion to how much of the county each hex covers.",
            "values": density,
        }
    ]
    for lid, (_, meta) in COUNTY_LENSES.items():
        values = [None] * grid.size
        for i, parts in enumerate(weights):
            vals = [(county[k][lid], p) for k, p in parts if county[k][lid] is not None]
            wsum = sum(p for _, p in vals)
            if wsum:
                v = sum(v * p for v, p in vals) / wsum
                values[i] = round(v, 4) if meta["format"] == "pct" else round(v, 1 if v < 1000 else 0)
        lenses.append({"id": lid, **meta, "source": "acs_5yr", "values": values})

    pct = world.get("pct")
    if pct:
        cover = world.get("cover") or [1] * grid.size  # cover 0 = no land-cover samples: no data, not 0%
        for lid, (keys, meta) in LAND_LENSES.items():
            values = [
                round(sum(pct[k][i] for k in keys) / 100, 2) if owner[i] >= 0 and cover[i] else None
                for i in range(grid.size)
            ]
            lenses.append(
                {
                    "id": lid, "group": "land", **meta, "unit": "share of land", "format": "pct",
                    "scale": "linear", "higher_is": "neutral", "source": "nlcd", "values": values,
                }
            )
    else:
        print("    _world has no land-cover shares (pct) yet: land lenses skipped")

    lenses += community_lenses(world["counties"], county, weights, acs)

    for lens in lenses:
        if "domain" in lens:  # fixed by the lens itself (categorical, or a clamped scale)
            continue
        lens["domain"] = percentile_domain(lens["values"], lens["scale"] == "log")
        lens["description"] += DOMAIN_NOTE

    groups = [g for g in GROUPS if any(l["group"] == g["id"] for l in lenses)]
    write_web("lenses.json", {"vintage": acs_payload["vintage"], "groups": groups, "lenses": lenses})


# ---------------------------------------------------------------- faith, wellbeing, disaster and veteran lenses
#
# Faith (2020 U.S. Religion Census), Wellbeing (id amenities; CDC PLACES), Disasters (OpenFEMA) and Veterans (ACS).
# They are appended after the land lenses so the existing lenses keep their positions. Each county value
# is spread to hexes exactly like the lenses above (population-weighted over the hex's county parts).
# The county formulas live here so counties.json computes the identical numbers.

FAITH_CATEGORIES = [
    {"id": "evangelical", "name": "Evangelical Protestant", "color": "#c8643b",
     "description": "ARDA's Evangelical Protestant tradition (e.g. Southern Baptists, non-denominational churches, Assemblies of God)."},
    {"id": "mainline", "name": "Mainline Protestant", "color": "#4f7cc0",
     "description": "ARDA's Mainline Protestant tradition (e.g. United Methodist, ELCA Lutheran, Presbyterian USA, Episcopal)."},
    {"id": "black_protestant", "name": "Black Protestant", "color": "#8a5bb5",
     "description": "ARDA's Black Protestant tradition (historically Black denominations; under-reported in 2020)."},
    {"id": "catholic", "name": "Catholic", "color": "#e3b23c", "description": "The Catholic Church."},
    {"id": "orthodox", "name": "Orthodox Christian", "color": "#2f9e95", "description": "Eastern and Oriental Orthodox churches."},
    {"id": "lds", "name": "Latter-day Saints", "color": "#6aa84f",
     "description": "The Church of Jesus Christ of Latter-day Saints (split out of ARDA's 'Other')."},
    {"id": "other", "name": "Other faiths", "color": "#9a9aa8",
     "description": "ARDA's 'Other' tradition without the Latter-day Saints: Jewish, Muslim, Buddhist, Hindu and other groups."},
]
FAITH_NOTE = ("Counts adherents of congregations where they worship, not residents' beliefs. About half of Americans "
              "are counted, and some traditions (notably historically Black churches) are under-reported. 2020 data. "
              "Connecticut not shown.")
AMENITY_MEASURES = ["mhlth", "depression", "ghlth", "lpa"]
AMENITIES_NOTE = "Model-based CDC estimates of adult health, not a happiness survey. No data for Kentucky or Pennsylvania (2025 release)."
DISASTERS_NOTE = "A declaration is an administrative act that unlocks federal aid, not a measure of damage."


def faith_county(rec: dict | None) -> dict | None:
    """USRC county record → {"faith": adherents per 100 residents, "trad": {category: adherents}}; None if no data."""
    if not rec or rec.get("adherents") is None or not rec.get("pop"):
        return None
    trad = {c["id"]: rec.get(c["id"]) or 0 for c in FAITH_CATEGORIES}
    trad["other"] = max((rec.get("other") or 0) - (rec.get("lds") or 0), 0)
    return {"faith": rec["adherents"] / rec["pop"] * 100, "trad": trad}


def dominant_tradition(trad: dict) -> int | None:
    """Index into FAITH_CATEGORIES of the largest tradition, or None if no adherents were reported."""
    best = max(range(len(FAITH_CATEGORIES)), key=lambda i: trad.get(FAITH_CATEGORIES[i]["id"], 0))
    return best if trad.get(FAITH_CATEGORIES[best]["id"], 0) > 0 else None


def amenities_by_county(places: dict, geoids: list[str]) -> tuple[dict, dict]:
    """geoid → amenities index (100 − 10 × mean z of AMENITY_MEASURES), plus the z-score reference stats.

    z-scores use the mean and standard deviation over the map's counties that have all four measures
    (unweighted). Counties missing any of them get None (never imputed)."""
    rows = {g: places.get(g) for g in geoids}
    full = {g: r for g, r in rows.items() if r and all(r.get(m) is not None for m in AMENITY_MEASURES)}
    stats = {}
    for m in AMENITY_MEASURES:
        vals = [r[m] for r in full.values()]
        mean = sum(vals) / len(vals)
        stats[m] = (mean, math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)))
    out = {g: (100 - 10 * sum((r[m] - stats[m][0]) / stats[m][1] for m in AMENITY_MEASURES) / len(AMENITY_MEASURES))
           if g in full else None for g, r in rows.items()}
    return out, stats


def veterans_share(rec: dict) -> float | None:
    return ratio(rec, ["B21001_002"], "B21001_001")


def _optional(name: str):
    try:
        return load_interim_payload(name)
    except FileNotFoundError:
        print(f"    ! interim {name} missing: its lens is skipped (run: uv run python -m pipeline fetch {name} && "
              f"uv run python -m pipeline process {name})")
        return None


def _spread(weights, cvals: list, digits: int) -> list:
    """Population-weighted mean of the county values over each hex's parts (None where no part has a value)."""
    out = [None] * len(weights)
    for i, parts in enumerate(weights):
        vals = [(cvals[k], p) for k, p in parts if cvals[k] is not None]
        wsum = sum(p for _, p in vals)
        if wsum:
            out[i] = round(sum(v * p for v, p in vals) / wsum, digits)
    return out


def community_lenses(geoids: list[str], county: list, weights, acs: dict) -> list[dict]:
    lenses = []
    usrc = _optional("usrc_2020")
    if usrc:
        fc = [faith_county(usrc["data"]["counties"].get(g)) if county[k] else None for k, g in enumerate(geoids)]
        trad_idx = []
        for i, parts in enumerate(weights):  # hex tradition: adherents of each tradition summed over the hex's county parts
            tot = Counter()
            for k, p in parts:
                if fc[k] and county[k]["pop"]:
                    for t, a in fc[k]["trad"].items():
                        tot[t] += a * p / county[k]["pop"]
            trad_idx.append(dominant_tradition(tot) if tot else None)
        common = {"group": "faith", "source": "usrc_2020", "note": FAITH_NOTE, "higher_is": "neutral"}
        lenses.append({
            "id": "faith", "name": "Faith", "icon": "l-faith", "emoji": "🕍", **common,
            "unit": "adherents per 100 residents", "format": "float", "scale": "linear", "domain": [0, 100],
            "ramp": ["#1a1033", "#3d2466", "#6b3d8f", "#a5649c", "#d99a8f", "#f7dca0"],
            "description": "Religious adherents per 100 residents in 2020: adherents reported by the congregations in "
            "each county (members, their children and regular attenders, counted where the congregation meets) ÷ "
            "the 2020 population × 100, population-weighted over a hex's counties. Computed from the counts, not "
            "the file's rate columns. A county with a large church drawing from its neighbours can exceed 100; the "
            "colours stop at 100. Source: 2020 U.S. Religion Census (ASARB), via the Association of Religion Data "
            "Archives (theARDA.com). Connecticut is blank because the census uses its 8 old counties.",
            "values": _spread(weights, [f["faith"] if f else None for f in fc], 1),
        })
        lenses.append({
            "id": "faith_tradition", "name": "Largest tradition", "icon": "l-faith-tradition", "emoji": "⛪", **common,
            "unit": "largest religious tradition by adherents", "format": "category", "scale": "linear",
            "categories": FAITH_CATEGORIES, "ramp": [c["color"] for c in FAITH_CATEGORIES],
            "domain": [0, len(FAITH_CATEGORIES) - 1],
            "description": "The religious tradition with the most adherents in 2020 (ARDA's six traditions, with "
            "Latter-day Saints split out of 'Other'), summing each tradition's adherents over the counties in the "
            "hex in proportion to how much of each county the hex covers. 'Largest' can mean a plurality of a small "
            "share of residents. Source: 2020 U.S. Religion Census (ASARB) via ARDA. Connecticut is blank.",
            "values": trad_idx,
        })

    places = _optional("cdc_places")
    if places:
        idx, stats = amenities_by_county(places["data"]["counties"], geoids)
        recipe = "; ".join(f"{m} {stats[m][0]:.1f}% ± {stats[m][1]:.1f}" for m in AMENITY_MEASURES)
        release = places["data"].get("release") or ""
        lenses.append({
            "id": "amenities", "group": "life", "name": "Wellbeing", "icon": "l-amenities", "emoji": "🎭",
            "unit": "index (100 = average county)", "format": "float", "scale": "linear", "higher_is": "good",
            "ramp": ["#3b0a1e", "#7a1f3d", "#b9534f", "#e3a35c", "#c9d97a", "#7ecf8e"],
            "source": "cdc_places", "note": AMENITIES_NOTE,
            "description": "A health and wellbeing index (standing in for the strategy-game Amenities) from CDC "
            f"PLACES {release} age-adjusted estimates for adults: "
            "100 − 10 × the mean z-score of four shares — frequent mental distress (mhlth), depression, fair or poor "
            "self-rated health (ghlth) and no leisure-time physical activity (lpa). Each z-score compares the county "
            f"with the mean ± standard deviation of all mapped counties ({recipe}). 110 means one standard deviation "
            "better than average on all four. Population-weighted over a hex's counties. Null where PLACES has no "
            "data (all of Kentucky and Pennsylvania in this release): never imputed.",
            "values": _spread(weights, [idx[g] if county[k] else None for k, g in enumerate(geoids)], 1),
        })

    fema = _optional("fema_declarations")
    if fema:
        per = county_declarations(fema["data"], geoids)
        start, as_of = fema["data"]["window_start"], fema["data"]["as_of"]
        lenses.append({
            "id": "disasters", "group": "hazards", "name": "Disasters", "icon": "l-disasters", "emoji": "🌪️",
            "unit": "major-disaster declarations", "format": "int", "scale": "linear", "higher_is": "bad",
            "ramp": ["#14161f", "#2e2a4a", "#5b3466", "#93405f", "#cf6a4a", "#f4b860"],
            "source": "fema_declarations", "note": DISASTERS_NOTE,
            "description": f"Distinct FEMA major-disaster (DR) declarations naming the county from {start} to {as_of}, "
            "each disaster counted once (Biological/COVID-19, emergency and fire-management declarations excluded; "
            "statewide declarations are not counted for counties). A hex shows the population-weighted mean of its counties, rounded to a whole number. "
            "Connecticut's planning regions count a disaster declared for any old county they overlap.",
            "values": [None if v is None else int(round(v))
                       for v in _spread(weights, [per[g]["total"] if county[k] else None for k, g in enumerate(geoids)], 1)],
        })

    vet = [veterans_share(acs.get("0500000US" + g, {})) if county[k] else None for k, g in enumerate(geoids)]
    if any(v is not None for v in vet):
        lenses.append({
            "id": "veterans", "group": "people", "name": "Veterans", "icon": "l-veterans", "emoji": "🎖️",
            "unit": "share of civilian adults who are veterans", "format": "pct", "scale": "linear", "higher_is": "neutral",
            "ramp": ["#0d1a12", "#1f3d2a", "#3b6340", "#6b8a4e", "#a9b46a", "#ece2a6"],
            "source": "acs_5yr",
            "description": "Share of the civilian population aged 18 and over who are veterans (ACS B21001 line 2 ÷ "
            "line 1), population-weighted over a hex's counties. Active-duty service members are not civilians "
            "and are not in this count.",
            "values": [round(v, 4) if v is not None else None for v in _spread(weights, vet, 6)],
        })
    return lenses


def check(web_dir) -> list[tuple[str, str]]:
    """Faith, wellbeing, disaster and veteran lenses: presence, notes, category shape and coverage (pipeline.check validates the general shape)."""
    import json
    from pathlib import Path

    path = Path(web_dir) / "lenses.json"
    if not path.exists():
        return [("NOTE", "lenses.json missing (pipeline.check reports it)")]
    doc = json.loads(path.read_text())
    by_id = {l.get("id"): l for l in doc.get("lenses", []) if isinstance(l, dict)}
    out = []
    expect = {"faith": "usrc_2020", "faith_tradition": "usrc_2020", "amenities": "cdc_places",
              "disasters": "fema_declarations", "veterans": "acs_5yr"}
    for lid, src in expect.items():
        lens = by_id.get(lid)
        if lens is None:
            out.append(("NOTE", f"lens {lid} not built (needs interim {src})"))
            continue
        vals = lens.get("values") or []
        n = sum(1 for v in vals if v is not None)
        if lid != "veterans" and not lens.get("note"):
            out.append(("FAIL", f"lens {lid}: the legend caveat (note) is required"))
        if lid == "faith_tradition":
            cats = lens.get("categories") or []
            counts = Counter(v for v in vals if v is not None)
            mix = ", ".join(f"{cats[i]['id']} {c}" for i, c in counts.most_common() if i < len(cats))
            out.append(("PASS", f"lens {lid}: {n} hexes; {mix}"))
        elif lid == "faith" and lens.get("domain") != [0, 100]:
            out.append(("FAIL", "lens faith: domain must be clamped to [0, 100]"))
        else:
            nums = sorted(v for v in vals if v is not None)
            if nums:
                out.append(("PASS", f"lens {lid}: {n} hexes, median {nums[len(nums) // 2]}, range {nums[0]}…{nums[-1]}"))
    return out
