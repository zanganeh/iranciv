"""Counties: one row per county on the map for the Explorer table (column-oriented).

The metrics are the county values behind the lenses (same ACS 5-year formulas, see lenses.py),
plus land area and density (Gazetteer), land-cover shares averaged over the county's hex samples
(world.py `pct`) and the cities.json id of the CBSA the county belongs to.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from ..core.geo import MapProjection
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web
from .cities import nearest_land
from .disasters import county_declarations
from .lenses import (AMENITIES_NOTE, COUNTY_LENSES, DISASTERS_NOTE, FAITH_CATEGORIES, FAITH_NOTE, _optional,
                     amenities_by_county, dominant_tradition, faith_county, ratio, veterans_share)

REQUIRES = ["_world", "census_gazetteer", "acs_5yr", "cbsa_delineation", "cities.json",
            "usrc_2020", "cdc_places", "fema_declarations"]
PRODUCES = ["counties.json"]

# Output key → lens id (lenses.COUNTY_LENSES); the lens supplies the formula and metadata.
METRICS = {
    "income": "income", "home_value": "home", "poverty": "poverty", "median_age": "age",
    "foreign_born": "foreign_born", "bachelors": "bachelors", "broadband": "broadband",
    "commute": "commute", "homeownership": "homeownership", "uninsured": "uninsured",
}
LAND = {  # output key → (world pct key, label, description)
    "forest": ("forest", "Forest", "Share of the county's land covered by forest (NLCD 41–43)"),
    "crop": ("crop", "Cropland", "Share of the county's land in cultivated crops (NLCD 82)"),
    "pasture": ("pasture", "Pasture", "Share of the county's land in pasture or hay (NLCD 81)"),
    "developed": ("developed", "Built-up", "Share of the county's land developed at low, medium or high intensity (NLCD 22–24)"),
}
LAND_NOTE = (", averaged over the hex samples that fall in the county (each hex's share counted once per sample). "
             "Null for small counties that no sample hits.")


def _field(key, label, fmt, unit, higher_is, source, description):
    return {"key": key, "label": label, "format": fmt, "unit": unit, "higher_is": higher_is,
            "source": source, "description": description}


def _county_text(desc: str) -> str:
    """A lens description reworded for a single county (drop the hex-weighting remarks)."""
    desc = desc.replace(" of the counties in each hex", "").replace(", weighted by population", "")
    return re.sub(r" A hex [^.]*\.", "", desc)


def fields() -> list[dict]:
    out = [
        _field("geoid", "FIPS", "text", None, "neutral", "census_gazetteer", "5-digit county FIPS code (state + county)."),
        _field("name", "County", "text", None, "neutral", "census_gazetteer", "County or county-equivalent name."),
        _field("state", "State", "text", None, "neutral", "census_gazetteer", "State postal abbreviation."),
        _field("population", "Population", "int", "people", "neutral", "acs_5yr", "Total population (ACS B01003)."),
        _field("land_km2", "Land area", "km2", "km²", "neutral", "census_gazetteer", "Land area (Census Gazetteer ALAND)."),
        _field("density", "Density", "float", "people / km²", "neutral", "acs_5yr",
               "Residents per km² of land: ACS population ÷ Gazetteer land area."),
    ]
    for key, lid in METRICS.items():
        meta = COUNTY_LENSES[lid][1]
        out.append(_field(key, meta["name"], meta["format"], meta["unit"], meta["higher_is"], "acs_5yr",
                          _county_text(meta["description"])))
    for key, (_, label, desc) in LAND.items():
        out.append(_field(key, label, "pct", "share of land", "neutral", "nlcd", desc + LAND_NOTE))
    out.append(_field("cbsa", "Metro area", "text", None, "neutral", "cbsa_delineation",
                      "cities.json id of the metro or micro area the county belongs to (OMB delineation); "
                      "null if none, or if that area is too small for ACS 1-year data."))
    out.append(_field("cell", "Map hex", "int", None, "neutral", "census_gazetteer",
                      "A representative US land hex: the one nearest the county's Gazetteer centre point (in its own "
                      "state where possible). Lets a county smaller than a hex still be found on the map."))
    out += community_fields()
    return out


def community_fields() -> list[dict]:
    """Faith, wellbeing, disaster and veteran columns, after `cell` (see docs/DATA_CONTRACTS.md counties.json)."""
    cats = "; ".join(f"{c['id']} = {c['name']}" for c in FAITH_CATEGORIES)
    return [
        _field("faith", "Faith", "float", "adherents per 100 residents", "neutral", "usrc_2020",
               "Religious adherents reported by congregations in the county ÷ 2020 population × 100 (2020 U.S. "
               "Religion Census, via ARDA). Can exceed 100 where a congregation draws from neighbouring counties. "
               + FAITH_NOTE),
        _field("faith_tradition", "Largest tradition", "text", None, "neutral", "usrc_2020",
               f"The religious tradition with the most adherents in 2020 ({cats}); null where none were reported. "
               + FAITH_NOTE),
        _field("amenities", "Wellbeing", "float", "index (100 = average county)", "good", "cdc_places",
               "Health and wellbeing index: 100 − 10 × the mean z-score of the CDC PLACES age-adjusted shares of adults with frequent mental "
               "distress, depression, fair or poor health and no leisure-time physical activity (z against all "
               "mapped counties). " + AMENITIES_NOTE),
        _field("mhlth", "Mental distress", "pct", "adults, age-adjusted", "bad", "cdc_places",
               "Share of adults reporting 14 or more days of poor mental health in the past 30 (CDC PLACES "
               "age-adjusted, model-based estimate)."),
        _field("loneliness", "Loneliness", "pct", "adults, age-adjusted", "bad", "cdc_places",
               "Share of adults who feel lonely always, usually or sometimes (CDC PLACES age-adjusted, model-based "
               "estimate). Not published for several states in the 2025 release."),
        _field("disasters", "Disasters", "int", "major-disaster declarations", "bad", "fema_declarations",
               "Distinct FEMA major-disaster (DR) declarations naming the county since 2000-01-01, each disaster once; "
               "Biological (COVID-19), emergency and fire-management declarations excluded. " + DISASTERS_NOTE),
        _field("veterans", "Veterans", "int", "people", "neutral", "acs_5yr",
               "Civilian veterans aged 18 and over (ACS B21001 line 2)."),
        _field("veterans_pct", "Veterans share", "pct", "of civilian adults", "neutral", "acs_5yr",
               "Veterans among the civilian population aged 18 and over (ACS B21001 line 2 ÷ line 1)."),
        _field("armed_forces", "Armed forces", "int", "people", "neutral", "acs_5yr",
               "Residents aged 16 and over on active duty in the armed forces, counted where they live (ACS B23025 line 6)."),
        _field("armed_forces_pct", "Armed forces share", "pct", "of people 16+", "neutral", "acs_5yr",
               "Active-duty armed forces among residents aged 16 and over (ACS B23025 line 6 ÷ line 1)."),
    ]


def community_values(geoids: list[str], acs: dict) -> dict[str, dict]:
    """geoid → the community_fields() column values (null where the source has no data or its interim is missing)."""
    usrc, places, fema = _optional("usrc_2020"), _optional("cdc_places"), _optional("fema_declarations")
    pl = places["data"]["counties"] if places else {}
    amen = amenities_by_county(pl, geoids)[0] if places else {}
    decl = county_declarations(fema["data"], geoids) if fema else {}
    out = {}
    for g in geoids:
        rec = acs.get("0500000US" + g, {})
        fc = faith_county(usrc["data"]["counties"].get(g)) if usrc else None
        trad = dominant_tradition(fc["trad"]) if fc else None
        p = pl.get(g) or {}
        vet = rec.get("B21001_002")
        af = rec.get("B23025_006")
        out[g] = {
            "faith": round(fc["faith"], 1) if fc else None,
            "faith_tradition": FAITH_CATEGORIES[trad]["id"] if trad is not None else None,
            "amenities": round(amen[g], 1) if amen.get(g) is not None else None,
            "mhlth": round(p["mhlth"] / 100, 4) if p.get("mhlth") is not None else None,
            "loneliness": round(p["loneliness"] / 100, 4) if p.get("loneliness") is not None else None,
            "disasters": decl[g]["total"] if g in decl else None,
            "veterans": round(vet) if vet is not None else None,
            "veterans_pct": _round(veterans_share(rec), "pct"),
            "armed_forces": round(af) if af is not None else None,
            "armed_forces_pct": _round(ratio(rec, ["B23025_006"], "B23025_001"), "pct"),
        }
    return out


def _round(v, fmt):
    if v is None:
        return None
    if fmt == "pct":
        return round(v, 4)
    return round(v, 1) if abs(v) < 1000 else round(v)


def build() -> None:
    world = load_interim("_world")
    gaz = load_interim("census_gazetteer")["counties"]
    acs_payload = load_interim_payload("acs_5yr")
    acs = acs_payload["data"]["geos"]
    deline = load_interim("cbsa_delineation")
    city_ids = {c["id"] for c in json.loads((WEB_DATA / "cities.json").read_text())}
    cbsa_of = {g: code for code, geoids in deline["counties"].items() if code in city_ids for g in geoids}
    pct, cover = world.get("pct"), world.get("cover")
    proj, grid, owner = MapProjection(**world["projection"]), HexGrid(**world["grid"]), world["owner"]
    state_idx = {fips: k for k, fips in enumerate(world["states"])}

    def rep_cell(geoid, g):
        if g.get("lon") is None or g.get("lat") is None:
            return None
        x, y = proj(g["lon"], g["lat"])
        c = nearest_land(grid, owner, max(grid.cell_at(x, y), 0), x, y, state_idx.get(geoid[:2], -9))
        return c if c >= 0 else None

    # Land shares: sum each sample's hex share, per county.
    land_sum = [Counter() for _ in world["counties"]]
    land_n = Counter()
    if pct and cover:
        for cell, s in enumerate(world["county_samples"]):
            if not s or not cover[cell] or world["owner"][cell] < 0:
                continue
            for k in s:
                if k < 0:
                    continue
                land_n[k] += 1
                for key, (pk, _, _) in LAND.items():
                    land_sum[k][key] += pct[pk][cell]

    extra = community_values(world["counties"], acs)
    fs = fields()
    columns = [f["key"] for f in fs]
    fmt = {f["key"]: f["format"] for f in fs}
    rows, no_acs = [], []
    for k, geoid in enumerate(world["counties"]):
        g = gaz.get(geoid, {})
        rec = acs.get("0500000US" + geoid, {})
        pop = rec.get("B01003_001")
        if pop is None:
            no_acs.append(geoid)
        area = g.get("aland_km2")
        row = {
            "geoid": geoid, "name": g.get("name"), "state": g.get("usps"),
            "population": round(pop) if pop is not None else None,
            "land_km2": round(area, 1) if area else None,
            "density": round(pop / area, 1) if pop is not None and area else None,
            **{key: _round(COUNTY_LENSES[lid][0](rec), fmt[key]) if pop is not None else None for key, lid in METRICS.items()},
            **{key: (round(land_sum[k][key] / land_n[k] / 100, 4) if land_n[k] else None) for key in LAND},
            "cbsa": cbsa_of.get(geoid),
            "cell": rep_cell(geoid, g),
            **extra[geoid],
        }
        rows.append([row[c] for c in columns])

    write_web("counties.json", {"vintage": acs_payload["vintage"], "fields": fs, "columns": columns, "rows": rows})
    in_cbsa = sum(1 for r in rows if r[columns.index("cbsa")])
    print(f"    {len(rows)} counties, {in_cbsa} in a cities.json CBSA, {sum(1 for k in range(len(rows)) if not land_n[k])} without land shares"
          + (f"; no ACS row for {', '.join(no_acs[:5])}" if no_acs else ""))
