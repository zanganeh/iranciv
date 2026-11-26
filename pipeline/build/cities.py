"""Cities = metro/micro areas with ACS 1-year data, placed at their principal city.

Each city keeps its true position (x, y) and gets one land cell for its banner:
the cell under the principal city, snapped to the nearest US land cell when that
is water or foreign, and nudged up to 2 rings away when a bigger city already
holds it. GDP (Gold) is the sum of the metro's counties from BEA CAGDP2.
Population growth (2021 → the ACS year) comes from the Census population estimates, which
cover every year on today's metro borders (see growth.py).
For the City view each city also gets its member counties, a hex footprint (cells where most
county samples are member counties), its yearly population series, ranks among all cities and
its 5 nearest peers by population (see docs/DATA_CONTRACTS.md).
"""

from __future__ import annotations

import math
import re

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.store import load_interim, load_interim_payload, write_web
from .growth import Growth
from .yields import GDP_INDUSTRIES, INDUSTRY_LINES, YIELDS, bea_combos, economy, gdp_profile, sum_counties

REQUIRES = ["_world", "census_gazetteer", "acs_1yr", "acs_5yr", "cbsa_delineation", "bea_gdp", "pep_population"]
PRODUCES = ["cities.json", "nation.json"]

NON_CONUS = {"AK", "HI", "PR"}
CAPITAL_CBSA = "47900"  # Washington-Arlington-Alexandria, DC-VA-MD-WV
DISPLAY_NAME = {"Boise City": "Boise"}  # official CBSA names that read oddly on a map
HYPHENATED = {"Winston-Salem", "Wilkes-Barre", "Sedro-Woolley", "Lee's Summit", "Coeur d'Alene"}
PLACE_SUFFIX = re.compile(
    r" (city|town|village|borough|municipality|CDP|city and borough|urban county|"
    r"metropolitan government|consolidated government|unified government)\b.*$"
)


def parse_cbsa_name(full: str) -> tuple[str, list[str]]:
    """'Dallas-Fort Worth-Arlington, TX Metro Area' → ('Dallas', ['TX'])."""
    base = re.sub(r" (Metro|Micro) Area$", "", full)
    cities, _, states = base.rpartition(", ")
    first = cities
    for h in HYPHENATED:
        if cities.startswith(h):
            first = h
            break
    else:
        first = re.split(r"--|-|/", cities)[0]
    return first.strip(), states.split("-")


def name_matches(pname: str, name: str) -> bool:
    return pname == name or pname.startswith((name + " ", name + "-", name + "/"))


def principal_place(places_by_state: dict, name: str, state: str):
    """Fallback: the Gazetteer place best matching `name` in `state` (exact name first, then largest)."""
    best = None
    for p in places_by_state.get(state, ()):
        pname = p["name"]
        if not name_matches(pname, name):
            continue
        rest = PLACE_SUFFIX.sub("", pname)
        exact = rest == name or rest.startswith(name + "-")
        score = (exact, p["aland_km2"])
        if best is None or score > best[0]:
            best = (score, p)
    return best[1] if best else None


def delineated_place(principals: list[dict], places_by_geoid: dict, name: str, states: list[str]):
    """The OMB principal city that the CBSA title names first, as a Gazetteer place."""
    for p in principals:
        place = places_by_geoid.get(p["place_geoid"])
        if place and name_matches(PLACE_SUFFIX.sub("", p["name"]), name) and place["usps"] in states:
            return place
    return None


def ring_search(grid: HexGrid, start: int, max_rings: int):
    """Yield (ring, cell) for every cell within `max_rings` steps of `start`, nearest rings first."""
    seen = {start}
    frontier = [start]
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


def nearest_land(grid: HexGrid, owner: list[int], cell: int, x: float, y: float, state: int, max_rings=8):
    """Nearest US land cell to (x, y), preferring `state`: the first ring with land, plus one more."""
    found_ring = None
    best = None
    for ring, c in ring_search(grid, cell, max_rings):
        if found_ring is not None and ring > found_ring + 1:
            break
        if owner[c] < 0:
            continue
        found_ring = ring if found_ring is None else found_ring
        cx, cy = grid.center(*grid.col_row(c))
        score = (owner[c] != state, math.hypot(cx - x, cy - y))
        if best is None or score < best[0]:
            best = (score, c)
    return best[1] if best else -1


def free_cell(grid: HexGrid, owner, taken: set, cell: int, x: float, y: float, state: int, max_rings=2):
    """Nearest untaken land cell within `max_rings`, preferring `state`; None if there is none."""
    best = None
    for ring, c in ring_search(grid, cell, max_rings):
        if owner[c] < 0 or c in taken:
            continue
        cx, cy = grid.center(*grid.col_row(c))
        score = (owner[c] != state, math.hypot(cx - x, cy - y))
        if best is None or score < best[0]:
            best = (score, c)
    return best[1] if best else None


FOOTPRINT_MAJORITY = 4  # of a cell's 7 county samples
PEERS = 5
RANK_KEYS = ["population", "gdp", "gdp_per_capita", "growth"] + [y["key"] for y in YIELDS]


def footprints(world: dict, members: dict[str, list[str]]) -> dict[str, set[int]]:
    """CBSA code → US land cells where at least FOOTPRINT_MAJORITY of the 7 county samples are member counties."""
    cbsa_of = {g: code for code, geoids in members.items() for g in geoids}
    by_index = [cbsa_of.get(g) for g in world["counties"]]
    out: dict[str, set[int]] = {}
    for cell, s in enumerate(world["county_samples"]):
        if not s or world["owner"][cell] < 0:
            continue
        votes: dict[str, int] = {}
        for k in s:
            code = by_index[k] if k >= 0 else None
            if code:
                votes[code] = votes.get(code, 0) + 1
        for code, n in votes.items():
            if n >= FOOTPRINT_MAJORITY:
                out.setdefault(code, set()).add(cell)
    return out


def pop_series(pep: dict | None, code: str, members: list[str] | None) -> dict | None:
    """{year: population} from the PEP metro file, or None (missing, or a different county list, as for growth)."""
    rec = pep["cbsa"].get(code) if pep else None
    if not rec or not rec.get("pop") or (members is not None and set(members) != set(rec["counties"])):
        return None
    return {str(y): (round(v) if v is not None else None) for y, v in zip(pep["years"], rec["pop"])}


def add_ranks(cities: list[dict]) -> None:
    """ranks: 1 = highest among all cities, null where the value is null (cities are sorted by population,
    so ties keep population order). peers: the PEERS cities closest in population (by ratio)."""
    def value(c, key):
        if key == "gdp_per_capita":
            return c["gdp"] / c["population"] if c.get("gdp") is not None and c.get("population") else None
        if key in ("population", "gdp", "growth"):
            return c.get(key)
        return c["yields"].get(key)

    for c in cities:
        c["ranks"] = {}
    for key in RANK_KEYS:
        have = [c for c in cities if value(c, key) is not None]
        have.sort(key=lambda c: -value(c, key))  # stable: ties stay in population order
        for r, c in enumerate(have, 1):
            c["ranks"][key] = r
        for c in cities:
            c["ranks"].setdefault(key, None)
    for i, c in enumerate(cities):
        lp = math.log(c["population"])
        near = sorted((abs(math.log(o["population"]) - lp), j) for j, o in enumerate(cities) if j != i)
        c["peers"] = [cities[j]["id"] for _, j in near[:PEERS]]


def build() -> None:
    world = load_interim("_world")
    gaz = load_interim("census_gazetteer")
    acs_payload = load_interim_payload("acs_1yr")
    acs = acs_payload["data"]
    acs5 = load_interim("acs_5yr")["geos"]
    deline = load_interim("cbsa_delineation")
    bea_payload = load_interim_payload("bea_gdp")
    bea = bea_payload["data"]
    proj = MapProjection(**world["projection"])
    grid = HexGrid(**world["grid"])
    owner = world["owner"]

    state_index = {fips: i for i, fips in enumerate(world["states"])}
    fips_of_usps = {g["usps"]: geoid[:2] for geoid, g in gaz["counties"].items()}
    places_by_state: dict[str, list] = {}
    for p in gaz["places"]:
        places_by_state.setdefault(p["usps"], []).append(p)
    places_by_geoid = {p["geoid"]: p for p in gaz["places"]}
    county_pop = {k[9:]: r.get("B01003_001") for k, r in acs5.items()}
    combos = bea_combos(bea, gaz["counties"])
    pep = load_interim("pep_population")
    growth = Growth(pep, acs["year"])
    no_growth: dict[str, list[str]] = {}

    cities = []
    fallbacks, centroid, split_gdp, no_gdp = [], [], [], []
    for cbsa_id, rec in acs["cbsa"].items():
        meta = gaz["cbsa"].get(cbsa_id)
        if not meta:
            continue
        short, states = parse_cbsa_name(meta["name"])
        if states[0] in NON_CONUS:
            continue
        econ_pop = rec.get("B01003_001")
        if not econ_pop:
            continue
        place = delineated_place(deline["principal_cities"].get(cbsa_id, []), places_by_geoid, short, states)
        if not place:
            place = principal_place(places_by_state, short, states[0])
            fallbacks.append(meta["name"])
        if place:
            lon, lat = place["lon"], place["lat"]
        else:
            lon, lat = meta["lon"], meta["lat"]
            centroid.append(meta["name"])
        x, y = proj(lon, lat)

        members = deline["counties"].get(cbsa_id)
        est: set = set()
        if members:
            lines, split = sum_counties(members, bea, combos, county_pop, est)
            if split:
                split_gdp.append(meta["name"])
        else:
            lines = None
        gdp = gdp_profile(lines, bea["year"], est)
        if gdp["total"] is None:
            no_gdp.append(meta["name"])

        g, why = growth.cbsa(cbsa_id, members)
        if why:
            no_growth.setdefault(why, []).append(meta["name"])

        cities.append(
            {
                "id": cbsa_id,
                "name": DISPLAY_NAME.get(short, short),
                "full": meta["name"],
                "states": states,
                "metro": meta["metro"],
                "capital": cbsa_id == CAPITAL_CBSA,
                "x": round(x, 1),
                "y": round(y, 1),
                "cell": grid.cell_at(x, y),
                **economy(rec, gdp),
                "growth": g,
                "growth_years": growth.years,
                "counties": sorted(members or []),
                "pop_series": pop_series(pep, cbsa_id, members),
            }
        )
    cities.sort(key=lambda c: -c["population"])

    # Banner cells: on US land, one city per cell where possible (bigger cities choose first).
    taken: set[int] = set()
    snapped, pulled, moved, shared = [], [], [], []
    for c in cities:
        own = {state_index.get(fips_of_usps.get(u), -1) for u in c["states"]}
        state = state_index.get(fips_of_usps.get(c["states"][0]), -1)
        cell = c["cell"]
        if cell < 0 or owner[cell] < 0:
            cell = nearest_land(grid, owner, max(cell, 0), c["x"], c["y"], state)
            snapped.append(c["name"])
        elif owner[cell] not in own:
            # A border city whose point falls just across the line: pull it back into its
            # first state if that state has a free cell within 2 rings (the same reach as below).
            alt = free_cell(grid, owner, taken, cell, c["x"], c["y"], state)
            if alt is not None and owner[alt] == state:
                cell = alt
                pulled.append(c["name"])
        if cell in taken:
            alt = free_cell(grid, owner, taken, cell, c["x"], c["y"], state)
            if alt is None:
                shared.append(c["name"])
            else:
                cell = alt
                moved.append(c["name"])
        taken.add(cell)
        c["cell"] = cell

    feet = footprints(world, deline["counties"])
    own_only = []
    for c in cities:
        cells = feet.get(c["id"], set())
        if c["cell"] not in cells:
            if not cells:
                own_only.append(c["name"])
            cells = cells | {c["cell"]}
        c["footprint"] = sorted(cells)
    add_ranks(cities)
    no_series = [c["name"] for c in cities if c["pop_series"] is None]

    print(f"    {len(cities)} cities; principal city from OMB list 2 for {len(cities) - len(fallbacks)}")
    for label, items in (
        ("placed by name heuristic (no OMB principal-city match)", fallbacks),
        ("placed at area centroid", centroid),
        ("snapped off water/foreign cells", snapped),
        ("pulled back across a state line into their own state", pulled),
        ("moved to a free neighbouring cell", moved),
        ("still sharing a cell", shared),
        ("GDP uses a population split of a BEA combined area", split_gdp),
        ("without GDP", no_gdp),
        ("with a one-hex footprint (no cell with a county-sample majority)", own_only),
        ("without a population series", no_series),
        *((f"without growth: {why}", names) for why, names in sorted(no_growth.items())),
    ):
        if items:
            print(f"    {len(items)} {label}: {', '.join(items[:8])}{' …' if len(items) > 8 else ''}")

    nat_rec = acs["geos"].get("0100000US", {})
    nation = economy(nat_rec, gdp_profile(bea["nation"], bea["year"]))
    nation["growth"] = growth.nation()
    nation["growth_years"] = growth.years
    write_web("cities.json", cities)
    write_web(
        "nation.json",
        {
            "vintage": acs_payload["vintage"],
            "year": acs["year"],
            "gdp_vintage": bea_payload["vintage"],
            "nation": nation,
            "yields": YIELDS,
            "industries": {k: label for k, (_, label) in INDUSTRY_LINES.items()},
            "gdp_industries": GDP_INDUSTRIES,
        },
    )
