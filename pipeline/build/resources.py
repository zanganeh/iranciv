"""Tile resources: the real power plants, mines and oil & gas plays, placed on the hex map.

Like strategic resources on a Civ tile, but only meaningful sites: each type has a size
threshold (stated in its description). Wind, solar, battery and geothermal plants are
usually split into many small EIA "plants" (Alta Wind I–XI, the Geysers' units), so those
are summed per hex first, counting that fuel's capacity in every plant (so the batteries of a
solar-plus-storage "hybrid" count as storage). Only generators in service (EIA status operating
or standby) count. Oil and gas plays cover large areas, so they are thinned to a
regular lattice of hexes (about one in four) so the map shows a field, not a wall of icons.

Each item is tagged with its state and (if any) the cities.json metro holding its hex's centre county,
and `summary` totals the mapped sites per type, nationally and per state.

`types` is listed in rank order (rarest/most significant first); the frontend can use that
order when only one icon fits a cell.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from ..core.geo import MapProjection, Shape
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["_world", "eia_860m", "eia_coal", "msha_mines", "eia_shale_plays", "census_boundaries",
            "cbsa_delineation", "cities.json"]
PRODUCES = ["resources.json"]

# --- thresholds (MW = net summer capacity) -------------------------------------
PLANT_MIN = {"hydro": 250, "coal": 1000, "gas": 1500, "pumped_storage": 1000}  # single plants, by fuel
CLUSTER_MIN = {"wind": 500, "solar": 500, "battery": 400, "geothermal": 100}  # summed per hex
COAL_MINE_MIN = 2_000_000  # short tons per year
METAL_MIN_EMPLOYEES = 100
STRATEGIC = {"rare_earths", "uranium", "lithium"}  # any active site with a real crew counts
STRATEGIC_MIN_EMPLOYEES = 10
OFFSHORE_RINGS = 3  # offshore wind snaps to US land up to this many hexes away (other sites: 1)
LATTICE = 2  # oil/gas: keep hexes whose axial q and r are both multiples of this (1 in 4)

ENERGY_SRC = "eia_860m"
THINNED = ("thinned to about one hex in four on a regular lattice where no two icons touch; a play too small to "
           "hold a lattice hex gets one icon near its centre, or is named in its neighbours' tooltips.")
IN_SERVICE = "Only generators in service (EIA status operating or standby) count; units out of service are left out."
ONE_ICON = ("A plant that qualifies under several fuels is shown once, as its largest qualifying fuel; "
            "the tooltip lists its other fuels.")
CLUSTER = ("Plants are summed per hex (large projects are filed as many EIA plants), counting this technology's "
           "capacity in every plant, including hybrids where it is not the main fuel.")


def _t(id, name, category, emoji, color, unit, description, source):
    return {"id": id, "name": name, "category": category, "emoji": emoji, "icon": f"r-{id}", "color": color,
            "unit": unit, "description": description, "source": source}


def _metal(id, name, emoji, color, what):
    if id in STRATEGIC:
        rule = (f"Every active MSHA-registered site with at least {STRATEGIC_MIN_EMPLOYEES} employees, mills included, "
                "as it is a scarce strategic mineral.")
    else:
        rule = (f"Active surface or underground mines with at least {METAL_MIN_EMPLOYEES} employees "
                "(MSHA's reported headcount, the only size measure available for every mine).")
    return _t(id, name, "mineral", emoji, color, "employees",
              f"{what} {rule} Mines of the same commodity in one hex are combined. "
              "Coordinates and employees are as reported to MSHA; some complexes report their head-office position.",
              "msha_mines")


TYPES = [
    _t("nuclear", "Nuclear plant", "energy", "☢️", "#e8d44d", "MW",
       "Every nuclear power plant with a reactor in service. Value is the plant's net summer capacity from EIA-860M. "
       "Reactors out of service are not counted, so idle or restarting plants (e.g. Palisades, Crane/TMI-1) are not "
       "shown until EIA lists them as operating again.", ENERGY_SRC),
    _t("hydro", "Hydroelectric dam", "energy", "💧", "#3fa7d6", "MW",
       f"Conventional hydro plants of at least {PLANT_MIN['hydro']:,} MW net summer capacity (EIA-860M). "
       "Value is the whole plant's capacity; a dam EIA files as one plant per state (Hoover Dam) is shown once, "
       f"summed. {IN_SERVICE}", ENERGY_SRC),
    _t("coal_mine", "Coal mine", "mineral", "⛏️", "#4a4038", "short tons/yr",
       f"Coal mines producing at least {COAL_MINE_MIN / 1e6:.0f} million short tons in the year "
       "(EIA mine-level production, placed with MSHA mine coordinates).", "eia_coal"),
    _metal("gold", "Gold mine", "🥇", "#d4af37", "Gold-ore mines."),
    _metal("copper", "Copper mine", "🥉", "#b87333", "Copper-ore mines."),
    _metal("iron", "Iron mine", "⛓️", "#8a4b2a", "Iron-ore (taconite) mines."),
    _metal("silver", "Silver mine", "🥈", "#b8c0c8", "Silver-ore mines."),
    _metal("platinum", "Platinum-group mine", "💍", "#9fb4c7", "Platinum and palladium mines."),
    _metal("molybdenum", "Molybdenum mine", "🔩", "#6f7f8f", "Molybdenum-ore mines."),
    _metal("lead_zinc", "Lead-zinc mine", "🪨", "#7d8a99", "Lead and zinc mines."),
    _metal("rare_earths", "Rare-earth mine", "🧲", "#b05fc7", "Rare-earth ore mines."),
    _metal("uranium", "Uranium mine/mill", "⚛️", "#6fcf3f",
           "Uranium mines and mills (in-situ recovery wellfields are mostly not in MSHA's list)."),
    _metal("lithium", "Lithium mine", "🧪", "#c7e3f0", "Lithium mineral mines (brine operations are not listed)."),
    _t("coal_plant", "Coal power plant", "energy", "🏭", "#5a5a5a", "MW",
       f"Plants with at least {PLANT_MIN['coal']:,} MW of coal capacity (EIA-860M). "
       f"Value is the whole plant's capacity. {ONE_ICON} {IN_SERVICE}", ENERGY_SRC),
    _t("gas_plant", "Gas power plant", "energy", "⚡", "#f08a24", "MW",
       f"Plants with at least {PLANT_MIN['gas']:,} MW of natural-gas capacity (EIA-860M). "
       f"Value is the whole plant's capacity. {ONE_ICON} {IN_SERVICE}", ENERGY_SRC),
    _t("geothermal", "Geothermal field", "energy", "♨️", "#d9534f", "MW",
       f"Geothermal capacity, shown where a hex totals at least {CLUSTER_MIN['geothermal']} MW (EIA-860M). "
       f"{CLUSTER} {IN_SERVICE}", ENERGY_SRC),
    _t("pumped_storage", "Pumped-storage hydro", "energy", "🔄", "#2f7fb8", "MW",
       f"Pumped-storage plants of at least {PLANT_MIN['pumped_storage']:,} MW (EIA-860M): grid batteries "
       f"made of two reservoirs. {IN_SERVICE}", ENERGY_SRC),
    _t("wind", "Wind farms", "energy", "🌬️", "#9fd8cb", "MW",
       f"Wind capacity, shown where a hex totals at least {CLUSTER_MIN['wind']} MW net summer capacity (EIA-860M). "
       f"{CLUSTER} Offshore wind farms are placed on the nearest US coastal hex (up to {OFFSHORE_RINGS} hexes away). "
       f"{IN_SERVICE}", ENERGY_SRC),
    _t("solar", "Solar farms", "energy", "☀️", "#ffc93c", "MW",
       f"Utility-scale solar capacity, shown where a hex totals at least {CLUSTER_MIN['solar']} MW net summer "
       f"capacity (EIA-860M; rooftop solar is not included). {CLUSTER} {IN_SERVICE}", ENERGY_SRC),
    _t("battery", "Battery storage", "energy", "🔋", "#7bc96f", "MW",
       f"Battery-storage capacity, shown where a hex totals at least {CLUSTER_MIN['battery']} MW (EIA-860M). "
       f"{CLUSTER} {IN_SERVICE}", ENERGY_SRC),
    _t("oil", "Oil (shale play)", "energy", "🛢️", "#3a2f2a", "km² play area",
       "Hexes inside a major tight-oil play (Permian, Bakken/Three Forks, Eagle Ford, Niobrara), "
       f"{THINNED} Oil vs gas follows EIA's tight-oil and shale-gas production charts. "
       "Outlines are geological plays, not production; value is the play's area.", "eia_shale_plays"),
    _t("gas", "Natural gas (shale play)", "energy", "🔥", "#8fb3c9", "km² play area",
       "Hexes inside a major shale-gas play (Marcellus, Utica, Haynesville, Barnett, Fayetteville, Woodford, Mancos), "
       f"{THINNED} Outlines are geological plays, not production; "
       "value is the play's area.", "eia_shale_plays"),
]
RANK = {t["id"]: i for i, t in enumerate(TYPES)}

# Major plays (EIA's tight-oil / shale-gas production-by-play series) → kind, in priority order: where plays
# overlap, the first listed names the hex (Bakken over Three Forks, Marcellus over Utica) and the others go in
# the detail. Keyed (basin, play); play None matches every play in the basin. Minor plays (Antrim, New Albany,
# Devonian Ohio shale, ...) are left out.
PLAYS = [
    (("Williston", "Bakken"), "oil"),
    (("Williston", "Three Forks"), "oil"),
    (("Permian", "Wolfcamp - Midland"), "oil"),
    (("Permian", "Spraberry"), "oil"),
    (("Permian", "Wolfcamp"), "oil"),
    (("Permian", "Bone Spring"), "oil"),
    (("Permian", None), "oil"),
    (("Western Gulf", "Eagle Ford"), "oil"),
    (("Denver Basin", "Niobrara"), "oil"),
    (("Powder River", "Niobrara"), "oil"),
    (("Powder River", "Niobrara- Mowry"), "oil"),
    (("Appalachian", "Marcellus"), "gas"),
    (("Appalachian", "Utica"), "gas"),
    (("TX-LA-MS Salt Basin", "Haynesville-Bossier"), "gas"),
    (("Ft. Worth", "Barnett"), "gas"),
    (("Arkoma", "Fayetteville"), "gas"),
    (("Arkoma", "Woodford-Caney"), "gas"),
    (("Anadarko", "Woodford"), "gas"),
    (("Ardmore", "Woodford"), "gas"),
    (("Uinta", "Mancos"), "gas"),
]
BASIN_LABEL = {"Permian": "Permian Basin", "Williston": "Williston Basin"}


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def _n(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _years(y) -> str:
    return "" if not y else (f", online {y[0]}" if y[0] == y[1] else f", online {y[0]}–{y[1]}")


GENERIC = {"solar", "wind", "energy", "project", "farm", "center", "the", "battery", "storage", "power", "bess",
           "llc", "ranch", "county", "geothermal", "plant", "unit"}
MINE_KIND = {"Surface": "Surface mine", "Underground": "Underground mine", "Facility": "Mill / processing plant"}


_SUFFIX = re.compile(r",?\s+(LLC|LL|L|Inc\.?|LP|L\.P\.)$", re.I)


def _tidy(name: str, recase: bool = False) -> str:
    """Display form of a filed name: drop company suffixes ('Desert Sunlight 300, LLC', EIA's truncated
    'Peabody Powder River Mining Ll') and fix ALL CAPS ('STILLWATER MINE' → 'Stillwater Mine')."""
    name = _SUFFIX.sub("", " ".join(name.split())).rstrip(",")
    return name.title() if recase and name.isupper() else name


def _cluster_name(group: list[tuple[str, float]]) -> str | None:
    """A shared project name for (plant name, MW) pairs: the two-word prefix holding most of the MW,
    used when at least two plants and 40% of the capacity carry it ('Alta Wind V', 'Alta Wind X' → 'Alta Wind')."""
    by_prefix = defaultdict(lambda: [0, 0.0])
    for name, mw in group:
        words = name.split()[:2]
        if len(words) == 2 and any(w.lower().strip(",") not in GENERIC for w in words):
            key = " ".join(words).rstrip(",")
            by_prefix[key][0] += 1
            by_prefix[key][1] += mw
    if not by_prefix:
        return None
    prefix, (n, mw) = max(by_prefix.items(), key=lambda kv: kv[1][1])
    total = sum(mw for _, mw in group)
    return prefix if n >= 2 and mw >= 0.4 * total else None


class Placer:
    """lon/lat → (x, y, US land cell). Points just offshore snap to a nearby US land hex.

    x/y are the true position; build() reconciles them with the cell at the end (see _settle).
    """

    def __init__(self, world):
        self.proj = MapProjection(**world["projection"])
        self.grid = HexGrid(**world["grid"])
        self.owner = world["owner"]

    def land_cell(self, x, y, rings: int = 1) -> int:
        """The US land cell at x/y, else the nearest one in the first ring (up to `rings`) holding any; -1 if none."""
        cell = self.grid.cell_at(x, y)
        if cell < 0:
            return -1
        if self.owner[cell] >= 0:
            return cell
        seen, frontier = {cell}, [cell]
        for _ in range(rings):
            ring = []
            for c in frontier:
                for n in self.grid.neighbours(c):
                    if n not in seen:
                        seen.add(n)
                        ring.append(n)
            land = [n for n in ring if self.owner[n] >= 0]
            if land:
                return min(land, key=lambda n: _dist2(self.grid.center(*self.grid.col_row(n)), (x, y)))
            frontier = ring
        return -1

    def __call__(self, lon, lat, rings: int = 1):
        x, y = self.proj(lon, lat)
        return x, y, self.land_cell(x, y, rings)


def _dist2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _item(type_id, name, x, y, cell, value, detail, state=None):
    """`state`: the state the source files the site under (abbr or full name), used by locate(); not published."""
    return {"type": type_id, "name": _tidy(name), "x": x, "y": y, "cell": cell, "value": value, "detail": detail,
            "_state": state}


PLANT_TYPE = {"coal": "coal_plant", "gas": "gas_plant"}
_STATE_TAG = re.compile(r"\s*\([A-Z]{2}\)$")  # 'Hoover Dam (AZ)' → 'Hoover Dam'


def _wmean(group, weight) -> tuple[float, float]:
    """Weighted mean x/y of (plant, x, y, ...) tuples."""
    total = sum(weight(g[0]) for g in group) or 1
    return (sum(g[1] * weight(g[0]) for g in group) / total, sum(g[2] * weight(g[0]) for g in group) / total)


def power_plants(place: Placer) -> list[dict]:
    plants = load_interim("eia_860m")["plants"]
    singles = defaultdict(list)  # (type, cell, name without state tag) → [(plant, x, y, fuel)]
    clusters = defaultdict(list)  # (category, cell) → [(plant, x, y)]
    for p in plants:
        fuels = p["fuels"]  # sorted by MW, largest first
        x, y, cell = place(p["lon"], p["lat"], rings=OFFSHORE_RINGS if p.get("offshore") else 1)
        if cell < 0:
            continue
        if "nuclear" in fuels:
            fuel = "nuclear"
        else:  # the largest fuel meeting its threshold (Jim Bridger: 1,070 MW gas, 1,049 MW coal → coal plant)
            fuel = next((k for k, f in fuels.items() if k in PLANT_MIN and f["mw"] >= PLANT_MIN[k]), None)
        if fuel:
            singles[(PLANT_TYPE.get(fuel, fuel), cell, _STATE_TAG.sub("", p["name"]))].append((p, x, y, fuel))
        for cat in CLUSTER_MIN.keys() & fuels.keys():
            clusters[(cat, cell)].append((p, x, y))

    items = []
    for (t, cell, base), group in singles.items():
        # usually one plant; several only where EIA files one site per state (Hoover Dam (AZ) + (NV))
        group.sort(key=lambda g: -g[0]["mw"])
        lead, fuel = group[0][0], group[0][3]
        mw = sum(g[0]["mw"] for g in group)
        x, y = _wmean(group, lambda p: p["mw"])
        gens = sum(g[0]["fuels"][fuel]["generators"] for g in group)
        spans = [g[0]["fuels"][fuel]["years"] for g in group if g[0]["fuels"][fuel]["years"]]
        years = [min(y0 for y0, _ in spans), max(y1 for _, y1 in spans)] if spans else None
        others = defaultdict(float)
        for g in group:
            for k, v in g[0]["fuels"].items():
                if k != fuel:
                    others[k] += v["mw"]
        also = [f"{k.replace('_', ' ')} {_fmt(v)} MW" for k, v in sorted(others.items(), key=lambda kv: -kv[1]) if v >= 1]
        idle = sum(g[0].get("idle_mw", 0) for g in group)
        states = "/".join(dict.fromkeys(g[0]["state"] for g in group))
        detail = (f"{_n(gens, 'reactor' if fuel == 'nuclear' else 'generator')}{_years(years)}, "
                  f"{lead['operator']}, {states}"
                  + (f"; also {', '.join(also)}" if also else "")
                  + (f"; {_fmt(idle)} MW out of service" if idle >= 1 else ""))
        items.append(_item(t, base if len(group) > 1 else lead["name"], x, y, cell, round(mw), detail, lead["state"]))

    for (cat, cell), group in clusters.items():
        mw = sum(p["fuels"][cat]["mw"] for p, _, _ in group)
        if mw < CLUSTER_MIN[cat]:
            continue
        group.sort(key=lambda g: -g[0]["fuels"][cat]["mw"])
        x, y = _wmean(group, lambda p: p["fuels"][cat]["mw"])
        lead = group[0][0]
        operators = list(dict.fromkeys(p["operator"] for p, _, _ in group))
        if len(group) == 1:
            name = lead["name"]
            detail = f"{_n(lead['fuels'][cat]['generators'], 'generator')}{_years(lead['fuels'][cat]['years'])}, " \
                     f"{lead['operator']}, {lead['state']}"
        else:
            name = _cluster_name([(p["name"], p["fuels"][cat]["mw"]) for p, _, _ in group]) \
                or f"{_tidy(lead['name'])} (+{len(group) - 1})"
            ops = ", ".join(_tidy(o) for o in operators[:2]) + (f" and {len(operators) - 2} more" if len(operators) > 2 else "")
            detail = f"{len(group)} plants incl. {lead['name']} ({_fmt(lead['fuels'][cat]['mw'])} MW); {ops}; {lead['state']}"
        if cat == "wind" and any(p.get("offshore") for p, _, _ in group):
            detail += "; offshore, shown on the nearest coast hex"
        hybrid = defaultdict(float)  # main fuel → this technology's MW at plants mainly running on that fuel
        for p, _, _ in group:
            if (main := next(iter(p["fuels"]))) != cat:
                hybrid[main.replace("_", " ")] += p["fuels"][cat]["mw"]
        if hybrid:
            detail += f"; {_fmt(sum(hybrid.values()))} MW at plants mainly {' or '.join(sorted(hybrid))}"
        items.append(_item(cat, name, x, y, cell, round(mw), detail, lead["state"]))
    return items


def coal_mines(place: Placer) -> tuple[list[dict], list[str]]:
    coal = load_interim("eia_coal")
    coords = load_interim("msha_mines")["coal"]
    items, missing = [], []
    for m in coal["mines"]:
        if m["tons"] < COAL_MINE_MIN:
            continue
        ll = coords.get(m["msha_id"])
        if not ll:
            missing.append(m["name"])
            continue
        x, y, cell = place(ll[1], ll[0])
        if cell < 0:
            continue
        emp = f", {_fmt(m['employees'])} employees" if m["employees"] else ""
        items.append(_item("coal_mine", m["name"], x, y, cell, m["tons"],
                           f"{m['type']} mine, {_tidy(m['operator'])}{emp}, {m['county']} County, {m['state']} ({coal['year']})",
                           m["state"]))
    return items, missing


def metal_mines(place: Placer) -> list[dict]:
    groups = defaultdict(list)
    for m in load_interim("msha_mines")["metal"]:
        c = m["commodity"]
        if m["status"] != "Active":
            continue
        if c in STRATEGIC:
            if (m["employees"] or 0) < STRATEGIC_MIN_EMPLOYEES:
                continue
        elif m["type"] == "Facility" or (m["employees"] or 0) < METAL_MIN_EMPLOYEES:
            continue
        x, y, cell = place(m["lon"], m["lat"])
        if cell >= 0:
            groups[(c, cell)].append((m, x, y))
    items = []
    for (c, cell), group in groups.items():
        group.sort(key=lambda g: -(g[0]["employees"] or 0))
        lead, x, y = group[0]
        emp = [m["employees"] for m, _, _ in group if m["employees"] is not None]
        value = sum(emp) if emp else None
        owner = lead["controller"] or lead["operator"]
        if len(group) == 1:
            detail = f"{MINE_KIND.get(lead['type'], lead['type'])}, {owner}, {lead['state']}"
        else:
            others = ", ".join(_tidy(m["name"], recase=True) for m, _, _ in group[1:4])
            detail = f"{len(group)} sites incl. {others}; {owner}, {lead['state']}"
        items.append(_item(c, _tidy(lead["name"], recase=True), x, y, cell, value, detail, lead["state"]))
    return items


def _play_rank(p) -> tuple[int, str] | None:
    for k, ((basin, name), kind) in enumerate(PLAYS):
        if p["basin"] == basin and (name is None or p["name"] == name):
            return k, kind
    return None


def _on_lattice(grid: HexGrid, cell: int) -> bool:
    """Regular 1-in-LATTICE² pattern: axial q and r both multiples of LATTICE (no two lattice hexes touch)."""
    col, row = grid.col_row(cell)
    q = col - (row - (row & 1)) // 2  # odd-r offset → axial
    return q % LATTICE == 0 and row % LATTICE == 0


def shale_plays(place: Placer) -> list[dict]:
    grid, proj = place.grid, place.proj
    plays = []
    for p in load_interim("eia_shale_plays")["plays"]:
        if ranked := _play_rank(p):
            shape = Shape([[proj(lon, lat) for lon, lat in ring] for ring in p["rings"]])
            plays.append((ranked[0], ranked[1], p, shape))
    plays.sort(key=lambda t: t[0])

    in_cell = defaultdict(list)  # US land cell → indices into plays whose outline holds its centre
    for k, (_, _, _, shape) in enumerate(plays):
        x0, y0, x1, y1 = shape.bbox
        for row in range(max(0, int(y0 / (1.5 * grid.radius))), min(grid.rows, int(y1 / (1.5 * grid.radius)) + 2)):
            for col in range(grid.cols):
                cx, cy = grid.center(col, row)
                if x0 <= cx <= x1 and shape.contains(cx, cy) and place.owner[grid.index(col, row)] >= 0:
                    in_cell[grid.index(col, row)].append(k)

    cells_of = defaultdict(list)  # play index → cells it names
    for cell, ks in in_cell.items():
        cells_of[ks[0]].append(cell)
    keep = {c for c in in_cell if _on_lattice(grid, c)}
    for k, cells in sorted(cells_of.items()):
        if keep.intersection(cells):
            continue
        # A small play still gets one icon: its most central hex that touches no other icon. If every hex
        # touches one, it is left to the tooltips that already name it ("also over the ... play"), and only
        # a play named nowhere breaks the spacing rule.
        free = [c for c in cells if not keep.intersection(grid.neighbours(c))]
        if not free and any(k in in_cell[c] for c in keep):
            continue
        pts = [grid.center(*grid.col_row(c)) for c in cells]
        mid = (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
        keep.add(min(free or cells, key=lambda c: _dist2(grid.center(*grid.col_row(c)), mid)))

    print(f"    shale plays: {len(in_cell)} US hexes inside a major play, {len(keep)} carry an icon")
    items = []
    for cell in sorted(keep):
        ks = in_cell[cell]
        _, kind, p, _ = plays[ks[0]]
        basin = BASIN_LABEL.get(p["basin"], p["basin"] + ("" if "Basin" in p["basin"] else " Basin"))
        also = list(dict.fromkeys(plays[k][2]["name"] for k in ks[1:] if plays[k][2]["name"] != p["name"]))
        x, y = grid.center(*grid.col_row(cell))
        items.append(_item(kind, f"{p['name']} play", x, y, cell,
                           round(p["area_km2"]) if p["area_km2"] else None,
                           f"{basin}; {p['lithology'].lower()}, {p['age']}"
                           + (f"; also over the {', '.join(also)} play{'s' if len(also) > 1 else ''}" if also else "")))
    return items


def _settle(items: list[dict], grid: HexGrid) -> int:
    """Round x/y, and move any point whose rounded position is not in its cell (offshore or snapped sites,
    a cluster's mean point) to that cell's centre, so x/y and cell always agree. Returns how many moved."""
    moved = 0
    for it in items:
        x, y = round(it["x"], 1), round(it["y"], 1)
        if grid.cell_at(x, y) != it["cell"]:
            x, y = (round(v, 1) for v in grid.center(*grid.col_row(it["cell"])))
            moved += 1
        it["x"], it["y"] = x, y
    return moved


NOT_ADDITIVE = {"oil", "gas"}  # each item carries its whole play's area: a sum would count a play many times
SUMMARY_NOTE = ("Totals of the mapped sites only: each type has a size threshold (see its description), so these are "
                "not a state's or the nation's full capacity or output. Oil and gas values are play areas, not summed.")


def locate(items: list[dict], world: dict) -> None:
    """Set each item's `state` and `cbsa` (cities.json id of the CBSA whose member counties hold the county at
    the cell's centre, or None). `state` is the state the source files the site under when that is a mapped
    state (Vogtle sits on the Savannah River, in a hex South Carolina owns, but is in Georgia), else the
    abbr of the cell's owner."""
    bounds = load_interim("census_boundaries")["states"]
    abbr_of = {s["fips"]: s["abbr"] for s in bounds}
    abbrs = [abbr_of.get(f) for f in world["states"]]
    known = {a for a in abbrs if a}
    by_name = {s["name"]: s["abbr"] for s in bounds} | {a: a for a in known}
    city_ids = {c["id"] for c in json.loads((WEB_DATA / "cities.json").read_text())}
    cbsa_of = {g: code for code, geoids in load_interim("cbsa_delineation")["counties"].items()
               if code in city_ids for g in geoids}
    owner, county, geoids = world["owner"], world["county"], world["counties"]
    for it in items:
        c = it["cell"]
        filed = by_name.get(it.pop("_state", None) or "")
        it["state"] = filed if filed in known else (abbrs[owner[c]] if owner[c] >= 0 else None)
        it["cbsa"] = cbsa_of.get(geoids[county[c]]) if county[c] >= 0 else None


def summarize(items: list[dict], types: list[dict]) -> dict:
    """National and per-state {type: {count, value, unit}} over the mapped items."""
    def blank(t):
        return {"count": 0, "value": None if t["id"] in NOT_ADDITIVE else 0, "unit": t["unit"]}

    by_id = {t["id"]: t for t in types}
    national = {t["id"]: blank(t) for t in types}
    states: dict[str, dict] = {}
    for it in items:
        t = by_id[it["type"]]
        rows = [national[t["id"]]]
        if it["state"]:
            rows.append(states.setdefault(it["state"], {}).setdefault(t["id"], blank(t)))
        for row in rows:
            row["count"] += 1
            if row["value"] is not None and it["value"] is not None:
                row["value"] += it["value"]
    for row in [*national.values(), *(r for st in states.values() for r in st.values())]:
        if row["value"] is not None:
            row["value"] = round(row["value"])
    return {"note": SUMMARY_NOTE, "national": national, "states": dict(sorted(states.items()))}


def build() -> None:
    world = load_interim("_world")
    place = Placer(world)
    items = power_plants(place)
    mines, missing = coal_mines(place)
    items += mines + metal_mines(place) + shale_plays(place)
    items.sort(key=lambda it: (RANK[it["type"]], -(it["value"] or 0)))
    moved = _settle(items, place.grid)

    locate(items, world)
    present = {it["type"] for it in items}
    types = [t for t in TYPES if t["id"] in present]
    write_web("resources.json", {"types": types, "items": items, "summary": summarize(items, types)})
    counts = defaultdict(int)
    for it in items:
        counts[it["type"]] += 1
    print("    " + ", ".join(f"{t['id']} {counts[t['id']]}" for t in TYPES))
    energy = sum(n for t, n in counts.items() if TYPES[RANK[t]]["source"] == ENERGY_SRC)
    print(f"    {len(items)} items ({energy} power plants); {moved} sat off their land hex and were moved to its centre")
    if missing:
        print(f"    ! {len(missing)} large coal mines without MSHA coordinates: {missing[:5]}")
