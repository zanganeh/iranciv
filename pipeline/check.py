"""Validate web/data/*.json against docs/DATA_CONTRACTS.md (v2) and report data quality.

    uv run python -m pipeline check

Part 1 checks each file's shape: required keys, types, per-cell array lengths
and value ranges, cross references (lens sources, resource types, state order).
Part 2 is a data-quality report: population conservation of the density lens,
cities on water or sharing a cell, null yields, rivers outside the map, counties
missing from the hex sampling and per-lens null shares.

Every line is PASS, WARN or FAIL (NOTE for information). The exit code is 1 if
anything FAILed. WARN thresholds are the constants below.

Part 3 runs builder-level checks: any module in pipeline/build/ may define

    def check(web_dir: Path) -> list[tuple[str, str]]   # [(level, message)], level in PASS/NOTE/WARN/FAIL

to validate the files it PRODUCES, so a new builder never has to edit this file. It is discovered
automatically (see run_builder_checks); a missing output should be reported as a NOTE (or WARN), not a FAIL,
unless the file is required. A check that raises is reported as a FAIL with the error.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .core.paths import INTERIM, WEB_DATA

# Density lens total vs ACS county total. Tight because lenses.py uses the same
# land-share formula, so this mainly catches dropped or double-counted people.
CONSERVATION_TOLERANCE = 0.005
LENS_NULL_WARN = 0.05  # share of US cells with a null lens value
CONUS_EXCLUDE = {"02", "15", "60", "66", "69", "72", "78"}  # AK, HI, territories

REQUIRED = ["world.json", "cities.json", "states.json", "nation.json", "lenses.json", "sources.json"]
OPTIONAL = ["resources.json", "counties.json"]

WATER_IDS = range(0, 4)
RELIEF_IDS = range(0, 4)
COVER_KEYS = [
    "none", "forest_deciduous", "forest_evergreen", "forest_mixed", "scrub", "grassland",
    "pasture", "cropland", "wetland", "urban", "barren", "ice",
]
PCT_KEYS = ["forest", "crop", "pasture", "grass", "shrub", "wetland", "developed"]
YIELD_KEYS = ["food", "production", "gold", "science", "culture"]
LENS_FORMATS = {"int", "usd", "pct", "float", "minutes", "years", "category"}
LENS_SCALES = {"log", "linear"}
HIGHER_IS = {"good", "bad", "neutral"}
RESOURCE_CATEGORIES = {"energy", "mineral", "agri"}
COLOR = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?([0-9a-fA-F]{2})?$")
ECON_NUMBERS = ["population", "employed", "median_income", "median_rent", "home_value", "unemployment", "rent_burden", "gdp"]
# The economic shape shared by a city, a state and nation.json "nation" (gdp_by_industry is optional).
ECON_KEYS = ["population", "employed", "yields", "jobs", "median_income", "median_rent", "home_value",
             "unemployment", "rent_burden", "gdp", "gdp_year", "growth", "growth_years"]
GROWTH_MAX = 0.25  # the pipeline nulls |growth| above this (see pipeline/build/growth.py)
CITY_RANK_BASE = ["population", "gdp", "gdp_per_capita", "growth"]
PEERS = 5
COUNTY_FORMATS = (LENS_FORMATS - {"category"}) | {"text", "km2"}  # categorical county columns are "text"
COUNTY_TEXT = ["geoid", "name", "state", "cbsa"]
NOT_ADDITIVE = {"oil", "gas"}  # resource types whose item values are play areas (summary value is null)


# ---------------------------------------------------------------- reporting


class Report:
    """Collects PASS/WARN/FAIL/NOTE lines and prints them as they come."""

    def __init__(self, quiet: bool = False):
        self.lines: list[tuple[str, str]] = []
        self.quiet = quiet

    def _add(self, level: str, msg: str) -> None:
        self.lines.append((level, msg))
        if not self.quiet:
            print(f"  {level:4}  {msg}")

    def ok(self, msg):
        self._add("PASS", msg)

    def warn(self, msg):
        self._add("WARN", msg)

    def fail(self, msg):
        self._add("FAIL", msg)

    def note(self, msg):
        self._add("NOTE", msg)

    def section(self, title):
        if not self.quiet:
            print(f"\n[{title}]")

    def count(self, level: str) -> int:
        return sum(1 for lv, _ in self.lines if lv == level)

    @property
    def failed(self) -> bool:
        return self.count("FAIL") > 0


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _sample(items, n=5) -> str:
    items = list(items)
    more = f" … (+{len(items) - n})" if len(items) > n else ""
    return ", ".join(str(x) for x in items[:n]) + more


def need_keys(r: Report, what: str, obj, keys) -> bool:
    """FAIL (once per object kind) if `obj` is not a dict or lacks keys. Returns True if all present."""
    if not isinstance(obj, dict):
        r.fail(f"{what}: expected an object, got {type(obj).__name__}")
        return False
    missing = [k for k in keys if k not in obj]
    if missing:
        r.fail(f"{what}: missing {', '.join(missing)}")
        return False
    return True


def check_cells(r: Report, name: str, arr, n: int, lo=None, hi=None, *, kind="int") -> bool:
    """A per-cell array: length n, every value an int (or number) within [lo, hi]."""
    if not isinstance(arr, list) or len(arr) != n:
        got = len(arr) if isinstance(arr, list) else type(arr).__name__
        r.fail(f"{name}: expected {n} values, got {got}")
        return False
    test = is_int if kind == "int" else is_num
    bad = [i for i, v in enumerate(arr) if not test(v) or (lo is not None and v < lo) or (hi is not None and v > hi)]
    if bad:
        r.fail(f"{name}: {len(bad)} values not {kind} in [{lo}, {hi}] (cells {_sample(bad)}; e.g. {arr[bad[0]]!r})")
        return False
    return True


# ---------------------------------------------------------------- validators


def check_world(r: Report, w) -> dict | None:
    """Validate world.json. Returns a small context (n, owner, states, …) for the other checks, or None."""
    if not isinstance(w, dict):
        r.fail("world.json: not an object")
        return None
    fails_before = r.count("FAIL")
    if w.get("version") != 2:
        r.fail(f"world.json: version is {w.get('version')!r}, expected 2 (still v1?)")
    if not need_keys(r, "world.json grid", w.get("grid"), ["radius", "cols", "rows"]):
        return None
    g = w["grid"]
    if not (is_num(g["radius"]) and g["radius"] > 0 and is_int(g["cols"]) and is_int(g["rows"]) and g["cols"] > 0 and g["rows"] > 0):
        r.fail(f"world.json grid: bad values {g}")
        return None
    n = g["cols"] * g["rows"]
    need_keys(r, "world.json", w, ["width", "height", "margin", "owner", "county", "elev", "water", "relief", "cover", "pct", "tables", "states", "counties", "rivers"])
    for k in ("width", "height", "margin"):
        if k in w and not is_num(w[k]):
            r.fail(f"world.json {k}: not a number ({w[k]!r})")

    states, counties = w.get("states") or [], w.get("counties") or []
    if not all(isinstance(s, dict) and {"fips", "abbr", "name"} <= s.keys() for s in states):
        r.fail("world.json states: every entry needs fips, abbr, name")
    if not all(isinstance(c, dict) and {"geoid", "name"} <= c.keys() for c in counties):
        r.fail("world.json counties: every entry needs geoid, name")

    ok = True
    owner = w.get("owner")
    ok &= check_cells(r, "world.json owner", owner, n, -2, len(states) - 1)
    ok &= check_cells(r, "world.json county", w.get("county"), n, -1, len(counties) - 1)
    ok &= check_cells(r, "world.json elev", w.get("elev"), n)
    if "elev_max" in w:
        ok &= check_cells(r, "world.json elev_max", w["elev_max"], n, 0, 9000)
    water = w.get("water")
    if "water" in w:
        ok &= check_cells(r, "world.json water", water, n, 0, 3)
    if "relief" in w:
        ok &= check_cells(r, "world.json relief", w["relief"], n, 0, 3)
    if "cover" in w:
        ok &= check_cells(r, "world.json cover", w["cover"], n, 0, len(COVER_KEYS) - 1)
    owner_ok = isinstance(owner, list) and len(owner) == n and all(is_int(v) for v in owner)

    if owner_ok and isinstance(water, list) and len(water) == n:
        wet_land = sum(1 for o, wt in zip(owner, water) if o != -1 and wt != 0)
        dry_water = sum(1 for o, wt in zip(owner, water) if o == -1 and wt == 0)
        if wet_land or dry_water:
            r.warn(f"world.json: owner/water disagree on {wet_land} land cells with water>0 and {dry_water} water cells with water=0")
        if isinstance(w.get("relief"), list) and len(w["relief"]) == n:
            bad = sum(1 for wt, rl in zip(water, w["relief"]) if is_int(wt) and wt != 0 and rl != 0)
            if bad:
                r.fail(f"world.json relief: {bad} water cells have relief != 0")
        elev = w.get("elev")
        if isinstance(elev, list) and len(elev) == n:
            high = [i for i, (wt, e) in enumerate(zip(water, elev)) if is_int(wt) and wt != 0 and is_num(e) and e > 0]
            if high:
                r.fail(f"world.json elev: {len(high)} water cells have elev > 0 (water elev is mean depth, <= 0; cells {_sample(high)})")

    pct = w.get("pct")
    if "pct" in w:
        if need_keys(r, "world.json pct", pct, PCT_KEYS):
            for k in PCT_KEYS:
                ok &= check_cells(r, f"world.json pct.{k}", pct[k], n, 0, 100)
            if owner_ok and all(isinstance(pct[k], list) and len(pct[k]) == n for k in PCT_KEYS):
                over = [i for i in range(n) if sum(pct[k][i] or 0 for k in PCT_KEYS if is_num(pct[k][i])) > 100 + len(PCT_KEYS)]
                outside = [i for i in range(n) if owner[i] < 0 and any(pct[k][i] for k in PCT_KEYS)]
                if over:
                    r.fail(f"world.json pct: {len(over)} cells sum above 100% (cells {_sample(over)})")
                if outside:
                    r.fail(f"world.json pct: {len(outside)} non-US cells have non-zero land-cover shares (must be 0)")

    tables = w.get("tables")
    if "tables" in w and need_keys(r, "world.json tables", tables, ["water", "relief", "cover"]):
        for t, ids in (("water", WATER_IDS), ("relief", RELIEF_IDS)):
            got = sorted(e.get("id") for e in tables[t] if isinstance(e, dict))
            if got != list(ids) or not all("name" in e for e in tables[t]):
                r.fail(f"world.json tables.{t}: expected ids {list(ids)} with names, got {got}")
        bad = [e for e in tables["cover"] if not isinstance(e, dict) or not is_int(e.get("id")) or not 0 <= e["id"] < len(COVER_KEYS)
               or e.get("key") != COVER_KEYS[e["id"]] or "name" not in e or (e["id"] and not COLOR.match(str(e.get("color", ""))))]
        if bad:
            r.fail(f"world.json tables.cover: {len(bad)} entries don't match the fixed cover ids/keys or lack name/color: {_sample(bad, 2)}")

    rivers = w.get("rivers") or []
    bad = [i for i, rv in enumerate(rivers) if not isinstance(rv, dict) or not {"name", "rank", "pts"} <= rv.keys()
           or not is_int(rv["rank"]) or not isinstance(rv["pts"], list) or len(rv["pts"]) < 4 or len(rv["pts"]) % 2 or not all(is_num(v) for v in rv["pts"])]
    if bad:
        r.fail(f"world.json rivers: {len(bad)} malformed (need name, int rank, pts=[x0,y0,x1,y1,…])")

    if ok and r.count("FAIL") == fails_before:
        r.ok(f"world.json: {g['cols']}×{g['rows']} grid, {len(states)} states, {len(counties)} counties, {len(rivers)} rivers")
    return {
        "n": n, "grid": g, "owner": owner if owner_ok else None, "states": states, "counties": counties,
        "width": w.get("width"), "height": w.get("height"), "rivers": rivers,
    }


def _check_econ(r: Report, what: str, obj: dict, yield_keys) -> None:
    """The economic fields shared by cities, states and the nation."""
    bad = [k for k in ECON_NUMBERS if k in obj and obj[k] is not None and not is_num(obj[k])]
    if bad:
        r.fail(f"{what}: non-numeric {', '.join(bad)}")
    y = obj.get("yields")
    if not isinstance(y, dict) or set(y) != set(yield_keys) or not all(v is None or is_num(v) for v in y.values()):
        r.fail(f"{what}: yields must have exactly {list(yield_keys)} with number|null values, got {y!r}"[:240])
    if not isinstance(obj.get("jobs"), dict) or not all(v is None or is_num(v) for v in obj["jobs"].values()):
        r.fail(f"{what}: jobs must be {{industry: number|null}}")
    for k in ("unemployment", "rent_burden"):
        v = obj.get(k)
        if is_num(v) and not 0 <= v <= 1:
            r.fail(f"{what}: {k}={v} is not a 0..1 fraction")
    g, gy = obj.get("growth"), obj.get("growth_years")
    if g is not None and not (is_num(g) and abs(g) <= GROWTH_MAX):
        r.fail(f"{what}: growth={g!r} must be null or a fraction within ±{GROWTH_MAX:.0%}")
    if gy is not None and not (isinstance(gy, list) and len(gy) == 2 and all(is_int(v) for v in gy) and gy[0] < gy[1]):
        r.fail(f"{what}: growth_years must be null or [earlier, later] ints, got {gy!r}")
    elif g is not None and gy is None:
        r.fail(f"{what}: growth without growth_years")


def check_growth(r: Report, docs: dict) -> None:
    """Growth invariants across files: one growth window everywhere, ending at nation.json's year,
    and every state and the nation have a rate whenever the window is set (their borders are fixed)."""
    nat = docs.get("nation.json") if isinstance(docs.get("nation.json"), dict) else None
    nation = nat.get("nation") if nat and isinstance(nat.get("nation"), dict) else None
    states = [s for s in docs.get("states.json") or [] if isinstance(s, dict)]
    cities = [c for c in docs.get("cities.json") or [] if isinstance(c, dict)]
    fixed = [("nation.json nation", nation)] if nation else []  # fixed borders: always a rate
    fixed += [(f"states.json {s.get('abbr') or s.get('fips')}", s) for s in states]
    rows = fixed + [(f"cities.json {c.get('id')}", c) for c in cities]
    windows = {tuple(o["growth_years"]) for _, o in rows if isinstance(o.get("growth_years"), list)}
    bad = 0
    if len(windows) > 1:
        r.fail(f"growth_years differ between rows: {sorted(windows)}")
        bad += 1
    year = nat.get("year") if nat else None
    for w in windows:
        if is_int(year) and len(w) == 2 and w[1] != year:
            r.fail(f"growth_years end in {w[1]} but nation.json year is {year} (growth must end at the year shown)")
            bad += 1
    for what, o in fixed:
        if o.get("growth_years") is not None and o.get("growth") is None:
            r.fail(f"{what}: growth is null although growth_years is set (states and the nation always have one)")
            bad += 1
    if not bad and windows:
        (w,) = windows
        r.ok(f"growth: one window {w[0]}→{w[1]} ending at nation.json year; every state and the nation have a rate")


def check_cities(r: Report, cities, ctx: dict | None, yield_keys=YIELD_KEYS) -> None:
    if not isinstance(cities, list) or not cities:
        r.fail("cities.json: expected a non-empty list")
        return
    keys = ["id", "name", "full", "states", "metro", "capital", "x", "y", "cell"] + ECON_KEYS
    fails_before = r.count("FAIL")
    missing = Counter(k for c in cities for k in keys if not isinstance(c, dict) or k not in c)
    if missing:
        r.fail(f"cities.json: missing keys {dict(missing)} (counts of cities)")
    ids = Counter(c.get("id") for c in cities)
    dup = [i for i, k in ids.items() if k > 1]
    if dup:
        r.fail(f"cities.json: duplicate ids {_sample(dup)}")
    bad_id = [c.get("id") for c in cities if not (isinstance(c.get("id"), str) and re.fullmatch(r"\d{5}", c["id"]))]
    if bad_id:
        r.fail(f"cities.json: ids must be 5-digit CBSA strings: {_sample(bad_id)}")
    pops = [c.get("population") for c in cities]
    if any(not is_num(p) or p <= 0 for p in pops):
        r.fail("cities.json: every city needs a positive population")
    elif pops != sorted(pops, reverse=True):
        r.fail("cities.json: not sorted by population, descending")
    capitals = [c.get("name") for c in cities if c.get("capital") is True]
    if len(capitals) != 1:
        r.warn(f"cities.json: expected exactly one capital (Washington), found {capitals}")
    n = ctx["n"] if ctx else None
    abbrs = {s.get("abbr") for s in ctx["states"] if isinstance(s, dict)} if ctx and ctx["states"] else None
    for c in cities:
        what = f"cities.json {c.get('id')} {c.get('name')}"
        if not (is_num(c.get("x")) and is_num(c.get("y"))):
            r.fail(f"{what}: x/y must be numbers")
        if not is_int(c.get("cell")) or (n is not None and not 0 <= c["cell"] < n):
            r.fail(f"{what}: cell {c.get('cell')!r} out of range")
        if not isinstance(c.get("states"), list) or not c.get("states"):
            r.fail(f"{what}: states must be a non-empty list")
        elif abbrs is not None and not set(c["states"]) <= abbrs:
            r.fail(f"{what}: states {sorted(set(map(str, c['states'])) - abbrs)} are not world.json state abbrs")
        if not (isinstance(c.get("metro"), bool) and isinstance(c.get("capital"), bool)):
            r.fail(f"{what}: metro and capital must be true/false")
        _check_econ(r, what, c, yield_keys)
        _check_city_view(r, what, c, ctx, set(ids))
        if r.count("FAIL") - fails_before > 20:
            r.fail("cities.json: too many errors, stopping")
            return
    _check_city_ranks(r, cities, CITY_RANK_BASE + list(yield_keys))
    if r.count("FAIL") == fails_before:
        r.ok(f"cities.json: {len(cities)} cities, footprints {sum(len(c.get('footprint') or []) for c in cities)} hexes, "
             f"ranks are 1..N permutations")


def _check_city_view(r: Report, what: str, c: dict, ctx: dict | None, ids: set) -> None:
    """counties, footprint, pop_series, peers of one city (ranks are checked across cities)."""
    fp = c.get("footprint")
    if not (isinstance(fp, list) and fp and all(is_int(v) for v in fp) and fp == sorted(set(fp))):
        r.fail(f"{what}: footprint must be a non-empty sorted list of distinct cell ints")
    else:
        if c.get("cell") not in fp:
            r.fail(f"{what}: footprint does not include the city's own cell {c.get('cell')}")
        owner = ctx["owner"] if ctx else None
        if owner and any(not 0 <= v < ctx["n"] or owner[v] < 0 for v in fp):
            r.fail(f"{what}: footprint has cells that are not US land")
    cs = c.get("counties")
    if not (isinstance(cs, list) and all(isinstance(g, str) and re.fullmatch(r"\d{5}", g) for g in cs)):
        r.fail(f"{what}: counties must be a list of 5-digit GEOID strings")
    ps = c.get("pop_series")
    if ps is not None and not (isinstance(ps, dict) and ps and all(re.fullmatch(r"\d{4}", str(y)) for y in ps)
                               and all(v is None or (is_num(v) and v > 0) for v in ps.values())):
        r.fail(f"{what}: pop_series must be null or {{year: positive number|null}}")
    peers, n_peers = c.get("peers"), min(PEERS, len(ids) - 1)
    if not (isinstance(peers, list) and len(peers) == n_peers and len(set(peers)) == len(peers)
            and c.get("id") not in peers and set(peers) <= ids):
        r.fail(f"{what}: peers must be {n_peers} distinct other city ids, got {peers!r}"[:200])


def _check_city_ranks(r: Report, cities: list, keys: list) -> None:
    """ranks: exactly `keys`; per key the non-null ranks are a permutation of 1..N, null exactly where the value is."""
    def value(c, k):
        if k == "gdp_per_capita":
            return c["gdp"] / c["population"] if is_num(c.get("gdp")) and is_num(c.get("population")) and c["population"] else None
        return c.get(k) if k in CITY_RANK_BASE else (c.get("yields") or {}).get(k)

    rows = [c for c in cities if isinstance(c, dict)]
    bad = [c.get("id") for c in rows if not isinstance(c.get("ranks"), dict) or set(c["ranks"]) != set(keys)]
    if bad:
        r.fail(f"cities.json ranks: must have exactly {keys} ({_sample(bad)})")
        return
    for k in keys:
        ranked = [(c["ranks"][k], value(c, k), c.get("id")) for c in rows]
        mismatch = [i for rk, v, i in ranked if (rk is None) != (v is None)]
        have = sorted((rk, v) for rk, v, _ in ranked if is_int(rk) and is_num(v))
        if mismatch:
            r.fail(f"cities.json ranks.{k}: rank and value disagree on null for {_sample(mismatch)}")
        if [rk for rk, _ in have] != list(range(1, len(have) + 1)):
            r.fail(f"cities.json ranks.{k}: not a permutation of 1..{len(have)}")
        elif any(have[i][1] < have[i + 1][1] - 1e-9 * abs(have[i + 1][1]) for i in range(len(have) - 1)):
            r.fail(f"cities.json ranks.{k}: rank order does not follow the value (1 = highest)")


def check_states(r: Report, states, ctx: dict | None, yield_keys=YIELD_KEYS) -> None:
    if not isinstance(states, list) or not states:
        r.fail("states.json: expected a non-empty list")
        return
    fails_before = r.count("FAIL")
    if ctx and [s.get("fips") for s in states] != [s.get("fips") for s in ctx["states"]]:
        r.fail("states.json: not in the same order (fips) as world.json states")
    for s in states:
        what = f"states.json {s.get('abbr') if isinstance(s, dict) else s!r}"
        if not need_keys(r, what, s, ["fips", "abbr", "name", "ranks"] + ECON_KEYS):
            continue
        _check_econ(r, what, s, yield_keys)
        ranks = s["ranks"]
        if not isinstance(ranks, dict) or set(ranks) != set(yield_keys) or not all(v is None or (is_int(v) and 1 <= v <= len(states)) for v in ranks.values()):
            r.fail(f"{what}: ranks must be {{yieldKey: 1..{len(states)}}} for exactly {list(yield_keys)}, got {ranks!r}"[:240])
        lab = s.get("label")
        if lab is not None and not (isinstance(lab, dict) and all(is_num(lab.get(k)) for k in ("x", "y", "angle", "size"))):
            r.fail(f"{what}: label must be {{x, y, angle, size}} numbers")
        land = s.get("land", "missing")
        if land is not None and not (isinstance(land, dict) and set(land) == set(PCT_KEYS)
                                     and all(is_num(v) and 0 <= v <= 1 for v in land.values())
                                     and sum(land.values()) <= 1 + 0.01 * len(PCT_KEYS)):
            r.fail(f"{what}: land must be null or {{{', '.join(PCT_KEYS)}}} 0..1 fractions, got {land!r}"[:240])
        _check_energy(r, what, s.get("energy", "missing"))
        if r.count("FAIL") - fails_before > 20:
            r.fail("states.json: too many errors, stopping")
            return
    _check_ranks(r, states, yield_keys)
    if r.count("FAIL") == fails_before:
        r.ok(f"states.json: {len(states)} states, ranks are 1..N permutations ordered by yield")


def _check_energy(r: Report, what: str, e) -> None:
    if e is None:
        return
    if not need_keys(r, f"{what} energy", e, ["unit", "total", "by_type", "sites", "note"]):
        return
    bt, st = e["by_type"], e["sites"]
    if e["unit"] != "MW" or not isinstance(e["note"], str) or not e["note"]:
        r.fail(f"{what} energy: unit must be 'MW' and note a non-empty string")
    if not (isinstance(bt, dict) and isinstance(st, dict) and set(bt) == set(st)
            and all(is_num(v) and v >= 0 for v in bt.values()) and all(is_int(v) and v >= 0 for v in st.values())):
        r.fail(f"{what} energy: by_type/sites must be {{type: MW >= 0}} / {{type: count}} with the same keys")
    elif not is_num(e["total"]) or abs(e["total"] - sum(bt.values())) > 1:
        r.fail(f"{what} energy: total {e['total']!r} != sum of by_type {sum(bt.values())}")
    elif any((bt[k] > 0) != (st[k] > 0) for k in bt):
        r.fail(f"{what} energy: MW and site counts disagree on which types are present")


def _check_ranks(r: Report, states: list, yield_keys) -> None:
    """Per yield key: ranks of states with a yield are a permutation of 1..N, null where the yield is null, 1 = largest."""
    rows = [s for s in states if isinstance(s, dict) and isinstance(s.get("ranks"), dict) and isinstance(s.get("yields"), dict)]
    for k in yield_keys:
        ranked = [(s["ranks"].get(k), s["yields"].get(k), s.get("abbr")) for s in rows]
        mismatch = [a for rk, y, a in ranked if (rk is None) != (y is None)]
        have = sorted((rk, y, a) for rk, y, a in ranked if is_int(rk) and is_num(y))
        if mismatch:
            r.fail(f"states.json ranks.{k}: rank and yield disagree on null for {_sample(mismatch)}")
        if [rk for rk, _, _ in have] != list(range(1, len(have) + 1)):
            r.fail(f"states.json ranks.{k}: not a permutation of 1..{len(have)}")
        elif any(have[i][1] < have[i + 1][1] for i in range(len(have) - 1)):
            r.fail(f"states.json ranks.{k}: rank order does not follow the yield (1 = largest)")


def check_nation(r: Report, nat) -> list[str]:
    """Validate nation.json; returns the yield keys it defines (for the city/state checks)."""
    if not need_keys(r, "nation.json", nat, ["vintage", "year", "gdp_vintage", "nation", "yields", "industries"]):
        return [y.get("key") for y in nat.get("yields", []) if isinstance(y, dict)] if isinstance(nat, dict) else YIELD_KEYS
    fails_before = r.count("FAIL")
    if not isinstance(nat["vintage"], str) or not is_int(nat["year"]):
        r.fail("nation.json: vintage must be a string and year an int")
    if nat["gdp_vintage"] is not None and not isinstance(nat["gdp_vintage"], str):
        r.fail("nation.json: gdp_vintage must be a string or null")
    keys = []
    for y in nat["yields"]:
        if not need_keys(r, f"nation.json yield {y.get('key') if isinstance(y, dict) else y!r}", y,
                         ["key", "icon", "emoji", "label", "color", "unit", "description"]):
            continue
        keys.append(y["key"])
        if y["unit"] not in ("workers", "usd") or not COLOR.match(str(y["color"])):
            r.fail(f"nation.json yield {y['key']}: unit must be workers|usd and color #hex")
    if not isinstance(nat["industries"], dict) or not all(isinstance(v, str) for v in nat["industries"].values()):
        r.fail("nation.json: industries must be {key: label}")
    if need_keys(r, "nation.json nation", nat["nation"], ECON_KEYS):
        _check_econ(r, "nation.json nation", nat["nation"], keys)
    if r.count("FAIL") == fails_before:
        r.ok(f"nation.json: {nat['vintage']}, yields {keys}")
    return keys


def check_lenses(r: Report, doc, ctx: dict | None, source_ids: set | None) -> None:
    if not need_keys(r, "lenses.json", doc, ["vintage", "groups", "lenses"]):
        return
    fails_before = r.count("FAIL")
    groups = {g.get("id") for g in doc["groups"] if isinstance(g, dict) and "name" in g}
    if len(groups) != len(doc["groups"]):
        r.fail("lenses.json groups: every group needs id and name")
    ids = Counter(l.get("id") for l in doc["lenses"] if isinstance(l, dict))
    if any(k > 1 for k in ids.values()):
        r.fail(f"lenses.json: duplicate lens ids {[i for i, k in ids.items() if k > 1]}")
    keys = ["id", "group", "name", "icon", "emoji", "unit", "format", "scale", "domain", "ramp", "higher_is", "source", "description", "values"]
    for lens in doc["lenses"]:
        what = f"lenses.json {lens.get('id') if isinstance(lens, dict) else lens!r}"
        if not need_keys(r, what, lens, keys):
            continue
        if lens["group"] not in groups:
            r.fail(f"{what}: unknown group {lens['group']!r}")
        if lens["format"] not in LENS_FORMATS or lens["scale"] not in LENS_SCALES or lens["higher_is"] not in HIGHER_IS:
            r.fail(f"{what}: format/scale/higher_is must be one of {sorted(LENS_FORMATS)}/{sorted(LENS_SCALES)}/{sorted(HIGHER_IS)}")
        d = lens["domain"]
        if not (isinstance(d, list) and len(d) == 2 and all(is_num(v) for v in d) and d[0] < d[1]) or (lens["scale"] == "log" and d[0] <= 0):
            r.fail(f"{what}: domain must be [lo, hi] with lo < hi (lo > 0 for log): {d!r}")
        if not (isinstance(lens["ramp"], list) and len(lens["ramp"]) >= 2 and all(COLOR.match(str(c)) for c in lens["ramp"])):
            r.fail(f"{what}: ramp must be ≥2 #hex colours")
        if source_ids is not None and lens["source"] not in source_ids:
            r.warn(f"{what}: source {lens['source']!r} is not in sources.json")
        if "note" in lens and not (lens["note"] is None or isinstance(lens["note"], str)):
            r.fail(f"{what}: note must be a string or null")
        if lens["format"] == "category":
            _check_categories(r, what, lens)
        elif "categories" in lens:
            r.fail(f"{what}: categories is only for format 'category'")
        vals = lens["values"]
        if ctx is None:
            continue
        if not isinstance(vals, list) or len(vals) != ctx["n"]:
            r.fail(f"{what}: values must have {ctx['n']} entries, got {len(vals) if isinstance(vals, list) else type(vals).__name__}")
            continue
        bad = [i for i, v in enumerate(vals) if v is not None and not is_num(v)]
        if bad:
            r.fail(f"{what}: {len(bad)} values are neither number nor null (cells {_sample(bad)})")
        if ctx["owner"]:
            off = [i for i, v in enumerate(vals) if v is not None and ctx["owner"][i] < 0]
            if off:
                r.fail(f"{what}: {len(off)} values on non-US cells (must be null)")
        if lens["format"] == "pct" and any(is_num(v) and not -1e-9 <= v <= 1 + 1e-9 for v in vals):
            r.fail(f"{what}: format pct but some values are outside 0..1 (pct lenses are fractions)")
        if lens["format"] == "category":
            n = len(lens["categories"]) if isinstance(lens.get("categories"), list) else 0
            if any(is_num(v) and not (is_int(v) and 0 <= v < n) for v in vals):
                r.fail(f"{what}: category values must be integer indices into categories (0..{n - 1}) or null")
    if r.count("FAIL") == fails_before:
        r.ok(f"lenses.json: {len(doc['lenses'])} lenses in {len(groups)} groups ({doc['vintage']})")


def _check_categories(r: Report, what: str, lens: dict) -> None:
    """A categorical lens: categories [{id, name, color}], ramp = their colours, linear domain [0, n-1]."""
    cats = lens.get("categories")
    if not (isinstance(cats, list) and len(cats) >= 2
            and all(isinstance(c, dict) and {"id", "name", "color"} <= c.keys() and COLOR.match(str(c["color"])) for c in cats)):
        r.fail(f"{what}: format category needs categories: [{{id, name, color}}] (≥2, #hex colours)")
        return
    n = len(cats)
    if [str(c).lower() for c in lens["ramp"]] != [str(c["color"]).lower() for c in cats]:
        r.fail(f"{what}: ramp must list the categories' colours in order")
    if lens["scale"] != "linear" or lens["domain"] != [0, n - 1]:
        r.fail(f"{what}: a categorical lens needs scale 'linear' and domain [0, {n - 1}]")
    if len({c["id"] for c in cats}) != n:
        r.fail(f"{what}: duplicate category ids")


def check_resources(r: Report, doc, ctx: dict | None, source_ids: set | None, city_ids: set | None = None) -> None:
    if not need_keys(r, "resources.json", doc, ["types", "items"]):
        return
    fails_before = r.count("FAIL")
    types = {}
    for t in doc["types"]:
        what = f"resources.json type {t.get('id') if isinstance(t, dict) else t!r}"
        if not need_keys(r, what, t, ["id", "name", "category", "emoji", "icon", "color", "unit", "description", "source"]):
            continue
        types[t["id"]] = t
        if t["category"] not in RESOURCE_CATEGORIES or not COLOR.match(str(t["color"])):
            r.fail(f"{what}: category must be {sorted(RESOURCE_CATEGORIES)} and color #hex")
        if source_ids is not None and t["source"] not in source_ids:
            r.warn(f"{what}: source {t['source']!r} is not in sources.json")
    abbrs = {s.get("abbr") for s in ctx["states"] if isinstance(s, dict)} if ctx and ctx["states"] else None
    bad_type, bad_shape, outside, bad_place = Counter(), 0, 0, 0
    for it in doc["items"]:
        if not isinstance(it, dict) or not {"type", "name", "x", "y", "cell", "value", "detail", "state", "cbsa"} <= it.keys():
            bad_shape += 1
            continue
        if it["type"] not in types:
            bad_type[it["type"]] += 1
        if (it["state"] is not None and abbrs is not None and it["state"] not in abbrs) or not (
                it["cbsa"] is None or (city_ids is None and isinstance(it["cbsa"], str)) or (city_ids and it["cbsa"] in city_ids)):
            bad_place += 1
        if not (is_num(it["x"]) and is_num(it["y"]) and is_int(it["cell"])) or (it["value"] is not None and not is_num(it["value"])):
            bad_shape += 1
        elif ctx and (not 0 <= it["cell"] < ctx["n"] or not (0 <= it["x"] <= (ctx["width"] or math.inf) and 0 <= it["y"] <= (ctx["height"] or math.inf))):
            outside += 1
    if bad_shape:
        r.fail(f"resources.json: {bad_shape} items malformed (need type, name, x, y, cell, value number|null, detail, state, cbsa)")
    if bad_place:
        r.fail(f"resources.json: {bad_place} items with a state that is not a world.json abbr or a cbsa that is not a cities.json id")
    _check_resource_summary(r, doc.get("summary"), types, [it for it in doc["items"] if isinstance(it, dict)])
    if bad_type:
        r.fail(f"resources.json: items with unknown types {dict(bad_type)}")
    if outside:
        r.warn(f"resources.json: {outside} items outside the map")
    if r.count("FAIL") == fails_before:
        per = Counter(it.get("type") for it in doc["items"] if isinstance(it, dict))
        r.ok(f"resources.json: {len(types)} types, {len(doc['items'])} items ({_sample(f'{k} {v}' for k, v in per.most_common())})")


def _check_resource_summary(r: Report, summary, types: dict, items: list) -> None:
    """summary.national/states recomputed from the items: counts and value sums must match."""
    if not need_keys(r, "resources.json summary", summary, ["note", "national", "states"]):
        return
    expect_n: dict = defaultdict(lambda: [0, 0])
    expect_s: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for it in items:
        v = it.get("value") if is_num(it.get("value")) else 0
        for row in [expect_n[it.get("type")]] + ([expect_s[it["state"]][it.get("type")]] if it.get("state") else []):
            row[0] += 1
            row[1] += v

    def same(what, got, want, t):
        if not (isinstance(got, dict) and {"count", "value", "unit"} <= got.keys()):
            return f"{what}: needs count, value, unit"
        if got["count"] != want[0] or (types.get(t) and got["unit"] != types[t]["unit"]):
            return f"{what}: count/unit {got['count']}/{got['unit']!r} do not match the items ({want[0]})"
        if t in NOT_ADDITIVE:
            return None if got["value"] is None else f"{what}: value must be null (play areas are not additive)"
        if not is_num(got["value"]) or abs(got["value"] - want[1]) > 1:
            return f"{what}: value {got['value']!r} != sum of item values {round(want[1])}"
        return None

    errs = []
    nat = summary["national"]
    if not isinstance(nat, dict) or set(nat) != set(types):
        errs.append(f"national must have exactly the types {sorted(types)}")
    else:
        errs += [e for t in types if (e := same(f"national.{t}", nat[t], expect_n[t], t))]
    st = summary["states"]
    if not isinstance(st, dict) or set(st) != set(expect_s):
        errs.append(f"states must have exactly the states with items {sorted(expect_s)}")
    else:
        for a, rows in expect_s.items():
            if not isinstance(st[a], dict) or set(st[a]) != set(rows):
                errs.append(f"states.{a}: types {sorted(st[a]) if isinstance(st[a], dict) else st[a]!r} != {sorted(rows)}")
                continue
            errs += [e for t in rows if (e := same(f"states.{a}.{t}", st[a][t], rows[t], t))]
    for e in errs[:5]:
        r.fail(f"resources.json summary {e}")


def check_counties(r: Report, doc, ctx: dict | None, city_ids: set | None) -> None:
    if not need_keys(r, "counties.json", doc, ["vintage", "fields", "columns", "rows"]):
        return
    fails_before = r.count("FAIL")
    fields, cols, rows = doc["fields"], doc["columns"], doc["rows"]
    fkeys = ["key", "label", "format", "unit", "higher_is", "source", "description"]
    if not (isinstance(fields, list) and all(isinstance(f, dict) and set(fkeys) <= f.keys() for f in fields)):
        r.fail(f"counties.json fields: each needs {', '.join(fkeys)}")
        return
    if [f["key"] for f in fields] != cols:
        r.fail("counties.json: fields[].key must equal columns, in order")
        return
    bad = [f["key"] for f in fields if f["format"] not in COUNTY_FORMATS or f["higher_is"] not in HIGHER_IS]
    if bad:
        r.fail(f"counties.json fields: bad format/higher_is for {_sample(bad)} (formats {sorted(COUNTY_FORMATS)})")
    missing = [k for k in ["geoid", "name", "state", "population", "land_km2", "density", "cbsa"] if k not in cols]
    if missing:
        r.fail(f"counties.json: missing columns {missing}")
        return
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(cols) for row in rows):
        r.fail(f"counties.json: rows must be lists of {len(cols)} values")
        return
    fmt = {f["key"]: f["format"] for f in fields}
    errs = Counter()
    for row in rows:
        rec = dict(zip(cols, row))
        for k, v in rec.items():
            if v is None:
                continue
            if fmt[k] == "text":
                if not isinstance(v, str):
                    errs[f"{k} not text"] += 1
            elif not is_num(v) or (fmt[k] == "pct" and not 0 <= v <= 1) or (k in ("population", "land_km2", "density") and v < 0):
                errs[f"{k} out of range"] += 1
        if not (isinstance(rec["geoid"], str) and re.fullmatch(r"\d{5}", rec["geoid"])):
            errs["geoid not 5 digits"] += 1
        if city_ids is not None and rec["cbsa"] is not None and rec["cbsa"] not in city_ids:
            errs["cbsa not a cities.json id"] += 1
    if errs:
        r.fail(f"counties.json: {dict(errs)}")
    if ctx and ctx["counties"]:
        want = [c.get("geoid") for c in ctx["counties"] if isinstance(c, dict)]
        if [row[cols.index("geoid")] for row in rows] != want:
            r.fail("counties.json: rows must be world.json counties, in the same order")
    if r.count("FAIL") == fails_before:
        r.ok(f"counties.json: {len(rows)} counties × {len(cols)} columns ({doc['vintage']})")


def check_sources(r: Report, doc) -> set | None:
    if not isinstance(doc, list):
        r.fail("sources.json: expected a list")
        return None
    fails_before = r.count("FAIL")
    keys = ["id", "title", "publisher", "homepage", "license", "description", "used_for", "vintage", "files"]
    ids = set()
    for s in doc:
        what = f"sources.json {s.get('id') if isinstance(s, dict) else s!r}"
        if not need_keys(r, what, s, keys):
            continue
        if s["id"] in ids:
            r.fail(f"{what}: duplicate id")
        ids.add(s["id"])
        bad = [f for f in s["files"] if not isinstance(f, dict) or not {"file", "url", "retrieved", "sha256", "bytes"} <= f.keys()]
        if bad:
            r.fail(f"{what}: {len(bad)} file entries lack file/url/retrieved/sha256/bytes")
        if not s["files"]:
            r.warn(f"{what}: no files cited (not fetched yet?)")
    if r.count("FAIL") == fails_before:
        r.ok(f"sources.json: {len(doc)} sources, {sum(len(s['files']) for s in doc if isinstance(s, dict))} files")
    return ids


# ---------------------------------------------------------------- data quality


def _interim(interim_dir: Path, name: str):
    p = interim_dir / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def county_population(acs5: dict, geoids) -> dict[str, float]:
    """ACS county population (B01003_001) for the given geoids, where known."""
    geos = acs5.get("data", {}).get("geos", {})
    out = {}
    for g in geoids:
        pop = geos.get("0500000US" + g, {}).get("B01003_001")
        if is_num(pop):
            out[g] = pop
    return out


def quality_density(r: Report, ctx: dict, lenses: dict, samples, county_pop: dict[str, float]) -> float | None:
    """Σ density × land area over US cells vs the ACS total of the map's counties. Returns the ratio."""
    dens = next((l for l in lenses.get("lenses", []) if isinstance(l, dict) and l.get("id") == "density"), None)
    if not dens or not isinstance(dens.get("values"), list) or len(dens["values"]) != ctx["n"] or not ctx["owner"]:
        r.note("density conservation: no usable density lens")
        return None
    if not county_pop:
        r.note("density conservation: no ACS county populations (acs_5yr interim missing)")
        return None
    hex_area = 1.5 * math.sqrt(3) * ctx["grid"]["radius"] ** 2
    total = 0.0
    for i, v in enumerate(dens["values"]):
        if v is None or ctx["owner"][i] < 0:
            continue
        s = samples[i] if samples and i < len(samples) else None
        land = sum(1 for k in s if k >= 0) / len(s) if s else 1.0
        total += v * hex_area * land
    acs_total = sum(county_pop.values())
    ratio = total / acs_total
    msg = (f"density conservation: Σ density × land area = {total / 1e6:.1f}M vs ACS county total {acs_total / 1e6:.1f}M "
           f"({ratio:.2%}; tolerance ±{CONSERVATION_TOLERANCE:.1%}; land share {'from _world samples' if samples else 'assumed 1'})")
    (r.ok if abs(ratio - 1) <= CONSERVATION_TOLERANCE else r.warn)(msg)
    return ratio


def quality_cities(r: Report, ctx: dict, cities: list) -> None:
    owner = ctx["owner"]
    if owner:
        wet = [(c["name"], c.get("population")) for c in cities if is_int(c.get("cell")) and 0 <= c["cell"] < ctx["n"] and owner[c["cell"]] < 0]
        (r.warn if wet else r.ok)(f"cities on non-land cells: {len(wet)}" + (f" ({_sample([n for n, _ in wet], 8)})" if wet else ""))
    by_cell = defaultdict(list)
    for c in cities:
        by_cell[c.get("cell")].append(c.get("name"))
    shared = {cell: names for cell, names in by_cell.items() if len(names) > 1}
    (r.warn if shared else r.ok)(
        f"cells holding more than one city: {len(shared)}" + (f" ({_sample(['+'.join(v) for v in shared.values()], 6)})" if shared else ""))


def quality_null_yields(r: Report, name: str, rows: list, published: dict | None = None) -> None:
    """Null and zero yields. A zero is a WARN (a stand-in for unknown?) unless `published`
    ({row id: {yield key: value}}, recomputed from the ACS interim) shows the source said 0 too."""
    nulls, zeros, confirmed = Counter(), Counter(), Counter()
    for row in rows:
        src = (published or {}).get(row.get("id")) or {}
        for k, v in (row.get("yields") or {}).items():
            if v is None:
                nulls[k] += 1
            elif v == 0:
                (confirmed if src.get(k) == 0 else zeros)[k] += 1
    r.note(f"{name} null yields: {dict(nulls) or 'none'}")
    if confirmed:
        r.note(f"{name} yields of 0 published as 0 by the ACS (small samples): {dict(confirmed)}")
    if zeros:
        r.warn(f"{name} yields exactly 0 (a stand-in for unknown?): {dict(zeros)}")


def published_city_yields(acs1: dict | None) -> dict:
    """{CBSA code: worker yields} recomputed from the acs_1yr interim, to tell real zeros from bugs."""
    if not acs1:
        return {}
    from .build.yields import economy

    return {code: economy(rec)["yields"] for code, rec in (acs1.get("data", {}).get("cbsa") or {}).items()}


def quality_growth(r: Report, name: str, rows: list) -> None:
    """How many rows have a growth rate (see pipeline/build/growth.py for when a city has none)."""
    have = [row["growth"] for row in rows if is_num(row.get("growth"))]
    if not rows:
        return
    years = next((row.get("growth_years") for row in rows if row.get("growth_years")), None)
    span = f" {years[0]}→{years[1]}" if isinstance(years, list) and len(years) == 2 else ""
    rng = f", range {min(have):+.1%} … {max(have):+.1%}" if have else ""
    r.note(f"{name} growth{span}: {len(have)} of {len(rows)} have a rate{rng}")


def quality_rivers(r: Report, ctx: dict) -> None:
    W, H = ctx["width"], ctx["height"]
    if not (is_num(W) and is_num(H)):
        return
    outside_all, stray = [], 0
    for rv in ctx["rivers"]:
        pts = rv.get("pts") if isinstance(rv, dict) else None
        if not isinstance(pts, list):
            continue
        inside = [0 <= pts[j] <= W and 0 <= pts[j + 1] <= H for j in range(0, len(pts) - 1, 2)]
        if not any(inside):
            outside_all.append(rv.get("name"))
        stray += inside.count(False)
    if outside_all:
        r.warn(f"rivers entirely outside the map: {len(outside_all)} ({_sample(outside_all)})")
    elif stray:
        r.warn(f"rivers: {stray} vertices outside the map (contract says clipped)")
    else:
        r.ok(f"rivers: all {len(ctx['rivers'])} inside the map")


def quality_counties(r: Report, ctx: dict, world_interim: dict | None, county_pop: dict[str, float], gaz: dict | None,
                     ratio: float | None = None) -> None:
    """Counties with ACS population that no hex sample hits, and whether their centre lands on a US cell.

    A group of such counties is a WARN unless the density lens conserves population to
    within the smaller of the tolerance and half that group's share. Dropping the group
    would move the ratio by its whole share, so only then is the ratio evidence that
    (at least most of) them are placed.
    """
    if not world_interim or "county_samples" not in world_interim:
        r.note("county coverage: _world interim missing")
        return
    geoids = world_interim.get("counties") or [c.get("geoid") for c in ctx["counties"]]
    hit = {k for s in world_interim["county_samples"] for k in s if k >= 0}
    hit |= {k for k in (world_interim.get("county") or []) if is_int(k) and k >= 0}
    unsampled = [(geoids[k], county_pop[geoids[k]]) for k in range(len(geoids)) if k not in hit and geoids[k] in county_pop]
    if not unsampled:
        r.ok("county coverage: every county with ACS population is hit by a hex sample")
        return
    total_pop = sum(county_pop.values())

    def consistent(pop: float) -> bool:
        return ratio is not None and abs(ratio - 1) < min(CONSERVATION_TOLERANCE, pop / total_pop / 2)

    un_pop = sum(p for _, p in unsampled)
    (r.note if consistent(un_pop) else r.warn)(
        f"county coverage: {len(unsampled)} counties ({un_pop / 1e6:.2f}M people, {un_pop / total_pop:.2%}) are hit by no sample; "
        + ("the density ratio is consistent with them being placed by their centre" if consistent(un_pop)
           else "the density ratio cannot confirm they are placed (builders must place them by their centre)"))
    if not (gaz and ctx["owner"] and "projection" in world_interim):
        return
    try:
        from .core.geo import MapProjection
        from .core.hexgrid import HexGrid

        proj, grid = MapProjection(**world_interim["projection"]), HexGrid(**ctx["grid"])
    except Exception as exc:  # geo.py is shared code; don't let it take the report down
        r.note(f"county coverage: cannot project centres ({exc})")
        return
    off = []
    for geoid, pop in unsampled:
        g = gaz.get("counties", {}).get(geoid)
        if not g:
            off.append((geoid, pop, "no gazetteer point"))
            continue
        cell = grid.cell_at(*proj(g["lon"], g["lat"]))
        if cell < 0 or ctx["owner"][cell] < 0:
            off.append((geoid, pop, g.get("name")))
    if off:
        off_pop = sum(p for _, p, _ in off)
        placed = consistent(off_pop)
        names = _sample([f"{n} {g}" for g, _, n in sorted(off, key=lambda t: -t[1])])
        (r.note if placed else r.warn)(
            f"county coverage: {len(off)} unsampled counties ({off_pop:,.0f} people) have their centre off US land: {names}; "
            + ("the density ratio is consistent with them being snapped onto land" if placed else "unless a builder snaps them they are dropped"))
    else:
        r.ok("county coverage: every unsampled county's centre is on a US land cell")


def quality_lens_nulls(r: Report, ctx: dict, lenses: dict) -> None:
    if not ctx["owner"]:
        return
    us = [i for i, o in enumerate(ctx["owner"]) if o >= 0]
    for lens in lenses.get("lenses", []):
        vals = lens.get("values") if isinstance(lens, dict) else None
        if not isinstance(vals, list) or len(vals) != ctx["n"] or not us:
            continue
        share = sum(1 for i in us if vals[i] is None) / len(us)
        (r.warn if share > LENS_NULL_WARN else r.ok)(f"lens {lens.get('id')}: {share:.1%} of {len(us)} US cells are null (warn above {LENS_NULL_WARN:.0%})")


# ---------------------------------------------------------------- entry point


def _load(r: Report, web_dir: Path, name: str, required: bool):
    p = web_dir / name
    if not p.exists():
        (r.fail if required else r.note)(f"{name}: missing" + ("" if required else " (optional, skipped)"))
        return None
    try:
        return json.loads(p.read_text())
    except ValueError as exc:
        r.fail(f"{name}: invalid JSON ({exc})")
        return None


LEVELS = ("PASS", "NOTE", "WARN", "FAIL")


def builder_checks() -> list[tuple[str, object]]:
    """(builder module name, check function) for every pipeline/build module that defines check(web_dir).

    Helpers without build() count too, so a module can check a file another builder writes.
    """
    import importlib
    import pkgutil

    from . import build as build_pkg

    out = []
    for m in sorted(pkgutil.iter_modules(build_pkg.__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"{build_pkg.__name__}.{m.name}")
        fn = getattr(mod, "check", None)
        if callable(fn):
            out.append((m.name, fn))
    return out


def run_builder_checks(r: Report, web_dir: Path, checks=None) -> None:
    """Run each builder's check(web_dir) and add its lines to the report (prefixed with the builder name)."""
    checks = builder_checks() if checks is None else checks
    if not checks:
        return
    r.section("builder checks")
    for name, fn in checks:
        try:
            lines = fn(web_dir) or []
        except Exception as exc:  # a broken check must not hide the rest of the report
            r.fail(f"{name}: check() raised {type(exc).__name__}: {exc}")
            continue
        for item in lines:
            try:
                level, msg = item
            except (TypeError, ValueError):
                r.fail(f"{name}: check() returned {item!r}, expected (level, message)")
                continue
            level = str(level).upper()
            if level not in LEVELS:
                r.fail(f"{name}: unknown level {level!r} for: {msg}")
                continue
            r._add(level, f"{name}: {msg}")


def run(web_dir: Path = WEB_DATA, interim_dir: Path = INTERIM, quiet: bool = False, builder_checks_=None) -> Report:
    """Run every check and return the report.

    builder_checks_: None = discover the builder check() functions (part 3); a list of (name, fn) to run those
    instead; [] to skip part 3.
    """
    r = Report(quiet)
    r.section("contracts (docs/DATA_CONTRACTS.md v2)")
    docs = {name: _load(r, web_dir, name, True) for name in REQUIRED}
    docs.update({name: _load(r, web_dir, name, False) for name in OPTIONAL})
    ctx = check_world(r, docs["world.json"]) if docs["world.json"] is not None else None
    source_ids = check_sources(r, docs["sources.json"]) if docs["sources.json"] is not None else None
    yield_keys = check_nation(r, docs["nation.json"]) if docs["nation.json"] is not None else YIELD_KEYS
    city_ids = None
    if docs["cities.json"] is not None:
        check_cities(r, docs["cities.json"], ctx, yield_keys)
        if isinstance(docs["cities.json"], list):
            city_ids = {c.get("id") for c in docs["cities.json"] if isinstance(c, dict)}
    if docs["states.json"] is not None:
        check_states(r, docs["states.json"], ctx, yield_keys)
    if docs["lenses.json"] is not None:
        check_lenses(r, docs["lenses.json"], ctx, source_ids)
    if docs["resources.json"] is not None:
        check_resources(r, docs["resources.json"], ctx, source_ids, city_ids)
    if docs["counties.json"] is not None:
        check_counties(r, docs["counties.json"], ctx, city_ids)
    check_growth(r, docs)

    r.section("data quality")
    if ctx is None:
        r.note("world.json unusable; skipping the data-quality report")
        run_builder_checks(r, web_dir, builder_checks_)
        return r
    wi = _interim(interim_dir, "_world")
    world_interim = wi.get("data") if wi else None
    acs5 = _interim(interim_dir, "acs_5yr")
    gaz = _interim(interim_dir, "census_gazetteer")
    geoids = [c.get("geoid") for c in ctx["counties"] if isinstance(c, dict)]
    geoids = [g for g in geoids if isinstance(g, str) and g[:2] not in CONUS_EXCLUDE]
    county_pop = county_population(acs5, geoids) if acs5 else {}
    samples = world_interim.get("county_samples") if world_interim else None
    ratio = None
    if isinstance(docs["lenses.json"], dict):
        ratio = quality_density(r, ctx, docs["lenses.json"], samples, county_pop)
        quality_lens_nulls(r, ctx, docs["lenses.json"])
    quality_counties(r, ctx, world_interim, county_pop, gaz.get("data") if gaz else None, ratio)
    if isinstance(docs["cities.json"], list):
        cities = [c for c in docs["cities.json"] if isinstance(c, dict)]
        quality_cities(r, ctx, cities)
        quality_null_yields(r, "cities", cities, published_city_yields(_interim(interim_dir, "acs_1yr")))
        quality_growth(r, "cities", cities)
    if isinstance(docs["states.json"], list):
        states = [s for s in docs["states.json"] if isinstance(s, dict)]
        quality_null_yields(r, "states", states)
        quality_growth(r, f"states ({len(states) - 1} + DC)" if any(s.get("abbr") == "DC" for s in states)
                       else "states", states)
    quality_rivers(r, ctx)
    run_builder_checks(r, web_dir, builder_checks_)
    return r


def main(web_dir: Path = WEB_DATA, interim_dir: Path = INTERIM) -> int:
    r = run(web_dir, interim_dir)
    print(f"\ncheck: {r.count('PASS')} pass, {r.count('WARN')} warn, {r.count('FAIL')} fail")
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
