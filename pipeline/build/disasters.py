"""disasters.json – FEMA major-disaster declarations by county and state, and NOAA billion-dollar disasters
by state (lazy-loaded by the Disasters advisor and the City View; see docs/DATA_CONTRACTS.md).

Also the shared helper `county_declarations()`, used by the Disasters lens (lenses.py) and the
counties.json `disasters` column, so all three count declarations the same way.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["_world", "fema_declarations", "noaa_billions"]
PRODUCES = ["disasters.json"]

FEMA_TYPES = [
    {"id": "hurricane", "name": "Hurricanes & tropical storms",
     "incident_types": ["Hurricane", "Tropical Storm", "Tropical Depression", "Typhoon"]},
    {"id": "flood", "name": "Floods & landslides", "incident_types": ["Flood", "Dam/Levee Break", "Mud/Landslide"]},
    {"id": "fire", "name": "Wildfires", "incident_types": ["Fire"]},
    {"id": "severe_storm", "name": "Severe storms & tornadoes",
     "incident_types": ["Severe Storm", "Severe Storm(s)", "Tornado", "Straight-Line Winds"]},
    {"id": "winter", "name": "Winter storms, ice & freezes",
     "incident_types": ["Winter Storm", "Snowstorm", "Severe Ice Storm", "Freezing"]},
    {"id": "other", "name": "Other (earthquakes, coastal storms, tsunamis, …)", "incident_types": []},
]
TYPE_IDS = [t["id"] for t in FEMA_TYPES]
_TYPE_OF = {it: t["id"] for t in FEMA_TYPES for it in t["incident_types"]}
NOAA_TYPES = [
    {"id": "drought", "name": "Drought"}, {"id": "flooding", "name": "Flooding"}, {"id": "freeze", "name": "Freeze"},
    {"id": "severe_storm", "name": "Severe storm"}, {"id": "tropical_cyclone", "name": "Tropical cyclone"},
    {"id": "wildfire", "name": "Wildfire"}, {"id": "winter_storm", "name": "Winter storm"},
]
RECENT_DAYS = 365
FEMA_RULE = ("Distinct major-disaster (DR) declarations naming the county since {start}, one per disaster number "
             "(FEMA lists New England towns separately under one county code); Biological (COVID-19) and emergency "
             "(EM) or fire-management (FM) declarations excluded; statewide or tribal designations count for the "
             "state only. Connecticut's planning regions count a disaster declared for any old county they overlap "
             "(Census Bureau town crosswalk). A declaration is an administrative act, not a measure of damage.")


def incident_type(raw: str) -> str:
    return _TYPE_OF.get(raw, "other")


def _map_counties(geoids, ct: dict, on_map: set) -> set:
    out = set()
    for g in geoids:
        for m in ct.get(g, [g]):
            if m in on_map:
                out.add(m)
    return out


def county_declarations(fema: dict, counties: list[str]) -> dict[str, dict]:
    """geoid → {"total", <type id>: n, "last_date"} for every county on the map (0 = none declared, a real zero)."""
    on_map = set(counties)
    ct = fema.get("ct_crosswalk", {})
    out = {g: {"total": 0, **{t: 0 for t in TYPE_IDS}, "last_date": None} for g in counties}
    for d in fema["disasters"].values():
        t = incident_type(d["type"])
        for g in _map_counties(d["counties"], ct, on_map):
            rec = out[g]
            rec["total"] += 1
            rec[t] += 1
            if rec["last_date"] is None or d["date"] > rec["last_date"]:
                rec["last_date"] = d["date"]
    return out


def build() -> None:
    world = load_interim("_world")
    fema_payload = load_interim_payload("fema_declarations")
    fema = fema_payload["data"]
    noaa_payload = load_interim_payload("noaa_billions")
    noaa = noaa_payload["data"]
    counties = world["counties"]
    on_map = set(counties)
    ct = fema["ct_crosswalk"]

    per = county_declarations(fema, counties)
    cols = ["geoid", "total", *TYPE_IDS, "last_date"]
    rows = [[g, *(per[g][c] for c in cols[1:])] for g in counties]

    states: dict[str, dict] = {}
    for d in fema["disasters"].values():
        s = states.setdefault(d["state"], {"total": 0, "statewide": 0, "by_type": {t: 0 for t in TYPE_IDS}})
        s["total"] += 1
        s["by_type"][incident_type(d["type"])] += 1
        s["statewide"] += 1 if d["statewide"] else 0

    as_of = fema["as_of"]
    cutoff = (date.fromisoformat(as_of) - timedelta(days=RECENT_DAYS)).isoformat()
    recent = []
    for num, d in fema["disasters"].items():
        if d["date"] < cutoff:
            continue
        mapped = sorted(_map_counties(d["counties"], ct, on_map))
        recent.append({"disaster": int(num), "type": incident_type(d["type"]), "incident_type": d["type"],
                       "title": d["title"].title(), "date": d["date"], "state": d["state"], "counties": mapped,
                       "statewide": d["statewide"], "areas": len(d["counties"])})
    recent.sort(key=lambda r: (r["date"], r["disaster"]), reverse=True)

    by_year: dict[int, list] = {}
    for e in noaa["events"]:
        y = by_year.setdefault(int(e["begin"][:4]), [0, 0])
        y[0] += 1
        y[1] += e["cost_usd"]
    y0, y1 = noaa["years"]
    events = noaa["events"]
    noaa_out = {
        "years": [y0, y1], "final": noaa["final"], "unit": "USD, CPI-adjusted",
        "rule": "Weather and climate disasters costing at least $1 billion each (CPI-adjusted). State figures "
        "are each state's share of the events that touched it; events span states, so never sum states.",
        "types": NOAA_TYPES,
        "states": {st: {"count": v.get("count"), "cost_usd": v.get("cost_usd")} for st, v in sorted(noaa["states"].items())},
        "national": {
            "events": len(events), "cost_usd": sum(e["cost_usd"] for e in events),
            "deaths": sum(e["deaths"] for e in events if e["deaths"] is not None),
            "by_year": [{"year": y, "events": by_year.get(y, [0, 0])[0], "cost_usd": by_year.get(y, [0, 0])[1]}
                        for y in range(y0, y1 + 1)],
            "costliest": sorted(events, key=lambda e: -e["cost_usd"])[:10],
        },
    }

    write_web("disasters.json", {
        "version": 1,
        "vintage": {"fema": fema_payload["vintage"], "noaa": noaa_payload["vintage"]},
        "sources": ["fema_declarations", "noaa_billions"],
        "fema": {
            "window": [fema["window_start"], as_of], "as_of": as_of,
            "rule": FEMA_RULE.format(start=fema["window_start"]),
            "types": FEMA_TYPES,
            "counties": {"columns": cols, "rows": rows},
            "states": dict(sorted(states.items())),
            "recent_days": RECENT_DAYS,
            "recent": recent,
        },
        "noaa": noaa_out,
    })
    hit = sum(1 for r in rows if r[1])
    top = max(rows, key=lambda r: r[1])
    print(f"    {len(fema['disasters'])} DR disasters; {hit} of {len(rows)} counties declared at least once "
          f"(most: {top[0]} with {top[1]}); {len(recent)} in the last {RECENT_DAYS} days")


# ---------------------------------------------------------------- check


def check(web_dir) -> list[tuple[str, str]]:
    web_dir = Path(web_dir)
    path = web_dir / "disasters.json"
    if not path.exists():
        return [("NOTE", "disasters.json not built (optional; run: uv run python -m pipeline build disasters --deps)")]
    out = []
    fail = lambda m: out.append(("FAIL", f"disasters.json: {m}"))
    doc = json.loads(path.read_text())
    for k in ("version", "vintage", "sources", "fema", "noaa"):
        if k not in doc:
            fail(f"missing key {k!r}")
    if any(lv == "FAIL" for lv, _ in out):
        return out
    fema, noaa = doc["fema"], doc["noaa"]
    try:
        world = json.loads((web_dir / "world.json").read_text())
        geoids = [c.get("geoid") if isinstance(c, dict) else c for c in world.get("counties") or []] or None
    except (OSError, ValueError):
        geoids = None
    cols = fema.get("counties", {}).get("columns")
    rows = fema.get("counties", {}).get("rows")
    type_ids = [t.get("id") for t in fema.get("types", [])]
    if cols != ["geoid", "total", *type_ids, "last_date"]:
        fail(f"fema.counties.columns {cols} do not match the types")
    elif not isinstance(rows, list):
        fail("fema.counties.rows missing")
    else:
        if isinstance(geoids, list) and [r[0] for r in rows] != geoids:
            fail("fema.counties rows are not in world.json counties order")
        bad = [r[0] for r in rows if len(r) != len(cols) or not all(isinstance(v, int) and v >= 0 for v in r[1:-1])
               or sum(r[2:-1]) != r[1] or (r[-1] is not None and not isinstance(r[-1], str))]
        if bad:
            fail(f"{len(bad)} county rows malformed or with types not summing to total (e.g. {bad[:3]})")
        else:
            hit = sum(1 for r in rows if r[1])
            out.append(("PASS", f"fema: {len(rows)} counties, {hit} with at least one declaration, window "
                                f"{fema.get('window')}, {len(fema.get('recent', []))} recent declarations"))
    for st, v in (fema.get("states") or {}).items():
        if sum(v.get("by_type", {}).values()) != v.get("total"):
            fail(f"fema.states.{st}: by_type does not sum to total")
            break
    recent = fema.get("recent", [])
    if [r.get("date") for r in recent] != sorted((r.get("date") for r in recent), reverse=True):
        fail("fema.recent is not newest first")
    nat = noaa.get("national", {})
    if not noaa.get("states") or not nat.get("by_year"):
        fail("noaa.states or noaa.national.by_year missing")
    elif sum(y["events"] for y in nat["by_year"]) != nat.get("events"):
        fail("noaa.national.by_year events do not sum to national.events")
    else:
        out.append(("PASS", f"noaa: {len(noaa['states'])} states/territories, {nat['events']} events "
                            f"{noaa.get('years')}, ${nat['cost_usd'] / 1e12:.2f}T CPI-adjusted"
                            + (" (final release)" if noaa.get("final") else "")))
    size = path.stat().st_size
    out.append(("PASS" if size < 600_000 else "NOTE", f"disasters.json is {size / 1e3:.0f} kB (lazy-loaded)"))
    return out
