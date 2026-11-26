"""The game-concept → real-data mapping, in one place.

Each yield is defined once here and exported to the browser, so the UI and the
Almanac always describe exactly what was computed. Gold is GDP (BEA); the
other yields count workers by industry where they live (ACS C24030).
"""

from __future__ import annotations

import re

# C24030 (Sex by Industry): line numbers in the "Male" block; the "Female"
# block repeats them 27 lines later.
INDUSTRY_LINES = {
    "agriculture": (4, "Agriculture, forestry, fishing & hunting"),
    "mining": (5, "Mining, quarrying, oil & gas"),
    "construction": (6, "Construction"),
    "manufacturing": (7, "Manufacturing"),
    "wholesale": (8, "Wholesale trade"),
    "retail": (9, "Retail trade"),
    "transportation": (11, "Transportation & warehousing"),
    "utilities": (12, "Utilities"),
    "information": (13, "Information"),
    "finance": (15, "Finance & insurance"),
    "real_estate": (16, "Real estate"),
    "professional": (18, "Professional, scientific & technical"),
    "management": (19, "Management of companies"),
    "admin": (20, "Administrative & waste services"),
    "education": (22, "Educational services"),
    "health": (23, "Health care & social assistance"),
    "arts": (25, "Arts, entertainment & recreation"),
    "accommodation": (26, "Accommodation & food services"),
    "other": (27, "Other services"),
    "public_admin": (28, "Public administration"),
}
FEMALE_OFFSET = 27

# BEA CAGDP2 line codes for the same industries (NAICS sectors). ACS "public
# administration" has no GDP twin: BEA's "government" line also covers public
# schools, hospitals and utilities run by governments, so it gets its own key.
GDP_LINES = {
    "agriculture": "3",
    "mining": "6",
    "construction": "11",
    "manufacturing": "12",
    "wholesale": "34",
    "retail": "35",
    "transportation": "36",
    "utilities": "10",
    "information": "45",
    "finance": "51",
    "real_estate": "56",
    "professional": "60",
    "management": "64",
    "admin": "65",
    "education": "69",
    "health": "70",
    "arts": "76",
    "accommodation": "79",
    "other": "82",
    "government": "83",
}
GDP_INDUSTRIES = {k: INDUSTRY_LINES[k][1] for k in GDP_LINES if k in INDUSTRY_LINES} | {
    "government": "Government & government enterprises (federal, state & local, incl. public schools & hospitals)"
}

YIELDS = [
    {
        "key": "food",
        "icon": "y-food",
        "emoji": "🌾",
        "label": "Food",
        "color": "#7fbf3f",
        "unit": "workers",
        "industries": ["agriculture"],
        "description": "Workers in agriculture, forestry, fishing & hunting, counted where they live (ACS C24030).",
    },
    {
        "key": "production",
        "icon": "y-production",
        "emoji": "⚙️",
        "label": "Production",
        "color": "#d9823b",
        "unit": "workers",
        "industries": ["manufacturing", "construction", "mining"],
        "description": "Workers in manufacturing, construction and mining, counted where they live (ACS C24030).",
    },
    {
        "key": "gold",
        "icon": "y-gold",
        "emoji": "💰",
        "label": "Gold",
        "color": "#f2c94c",
        "unit": "usd",
        "industries": [],
        "description": "Gross domestic product per year in current dollars, counted where the work is done (BEA "
        "CAGDP2, all industries). Metro GDP is the sum of the metro's counties (OMB 2023 delineations); for "
        "Virginia's combined county + independent city areas BEA reports one figure, split here by population "
        "only where the parts fall in different metros.",
    },
    {
        "key": "science",
        "icon": "y-science",
        "emoji": "🔬",
        "label": "Science",
        "color": "#4fa3e0",
        "unit": "workers",
        "industries": ["professional", "information"],
        "description": "Workers in professional, scientific & technical services and the information sector, counted "
        "where they live (ACS C24030).",
    },
    {
        "key": "culture",
        "icon": "y-culture",
        "emoji": "🎭",
        "label": "Culture",
        "color": "#b07ce0",
        "unit": "workers",
        "industries": ["arts"],
        "description": "Workers in arts, entertainment & recreation, counted where they live (ACS C24030).",
    },
]


def industries(rec: dict) -> dict[str, int | None]:
    """Workers per industry; None when the table is missing for this geography."""
    out = {}
    for key, (line, _) in INDUSTRY_LINES.items():
        m = rec.get(f"C24030_{line:03d}")
        f = rec.get(f"C24030_{line + FEMALE_OFFSET:03d}")
        out[key] = None if m is None and f is None else int((m or 0) + (f or 0))
    return out


def economy(rec: dict, gdp: dict | None = None) -> dict:
    """Yields + economic profile for one ACS geography record, plus optional GDP.

    `gdp` is {"total": usd|None, "year": int, "by_industry": {key: usd|None}}.
    """
    jobs = industries(rec)
    gdp = gdp or {}
    yields = {}
    for y in YIELDS:
        if y["key"] == "gold":
            yields["gold"] = gdp.get("total")
        else:
            vals = [jobs[k] for k in y["industries"]]
            yields[y["key"]] = None if any(v is None for v in vals) else sum(vals)
    labor = rec.get("B23025_003")
    unemployed = rec.get("B23025_005")
    income = rec.get("B19013_001")
    rent = rec.get("B25064_001")
    return {
        "population": rec.get("B01003_001"),
        "yields": yields,
        "jobs": jobs,
        "employed": rec.get("C24030_001"),
        "median_income": income,
        "median_rent": rent,
        "home_value": rec.get("B25077_001"),
        "unemployment": round(unemployed / labor, 4) if labor and unemployed is not None else None,
        "rent_burden": round(rent * 12 / income, 4) if rent and income else None,
        "gdp": gdp.get("total"),
        "gdp_year": gdp.get("year"),
        "gdp_by_industry": gdp.get("by_industry"),
        "gdp_undisclosed": gdp.get("undisclosed"),
        "gdp_estimated": gdp.get("estimated"),
    }


# CAGDP2 lines that are the sum of other lines. BEA withholds (D) a county's line to protect one business, but the
# parent and the siblings are often published, so the withheld value follows by subtraction.
GDP_IDENTITIES = [
    ("1", ["2", "83"]),
    ("2", ["91", "92"]),
    ("91", ["87", "11", "12"]),
    ("92", ["10", "88", "36", "45", "50", "59", "68", "75", "82"]),
    ("87", ["3", "6"]),
    ("88", ["34", "35"]),
    ("89", ["36", "10"]),
    ("90", ["12", "45"]),
    ("12", ["13", "25"]),
    ("50", ["51", "56"]),
    ("59", ["60", "64", "65"]),
    ("68", ["69", "70"]),
    ("75", ["76", "79"]),
]


# Apportioning order: the finest groups first, so a sector's remainder is split among as few lines as possible.
APPORTION_ORDER = ["87", "88", "89", "90", "12", "50", "59", "68", "75", "91", "92", "2", "1"]


def _exact(out: dict) -> bool:
    changed = False
    for parent, kids in GDP_IDENTITIES:
        terms = [parent, *kids]
        if any(t not in out for t in terms):
            continue
        unknown = [t for t in terms if out[t] is None]
        if len(unknown) != 1:
            continue
        u = unknown[0]
        known = sum(out[k] for k in kids if k != u)
        out[u] = round(out[parent] - known) if u != parent else round(known)
        changed = True
    return changed


def fill_suppressed(lines: dict, ref: dict | None = None, estimated: set | None = None) -> dict:
    """{line: usd|None} with withheld lines recovered.

    First exactly: an identity (parent = sum of its lines) with one unknown term gives it by subtraction. BEA
    suppresses complementary cells, so a county often has two or more unknown lines under a known parent; with
    `ref` (a fully disclosed row set, e.g. the nation) that remainder is then split among them in proportion to
    `ref`, and those lines are added to `estimated`.
    """
    out = dict(lines)
    while _exact(out):
        pass
    if ref is None:
        return out
    idents = dict(GDP_IDENTITIES)
    progress = True
    while progress:
        progress = False
        for parent in APPORTION_ORDER:
            kids = idents[parent]
            if out.get(parent) is None or any(k not in out for k in kids):
                continue
            unknown = [k for k in kids if out[k] is None]
            if len(unknown) < 2:
                continue
            rest = max(0.0, out[parent] - sum(out[k] for k in kids if out[k] is not None))
            weights = [max(0.0, ref.get(k) or 0.0) for k in unknown]
            tw = sum(weights)
            for k, wgt in zip(unknown, weights):
                out[k] = round(rest * (wgt / tw if tw > 0 else 1 / len(unknown)))
                if estimated is not None:
                    estimated.add(k)
            while _exact(out):
                pass
            progress = True
    return out


def gdp_profile(lines: dict | None, year: int, estimated: set | None = None) -> dict:
    """A BEA row set {line: usd|None} → the `gdp` argument of economy(). Withheld lines are recovered from their
    parent and sibling lines where possible; what stays withheld is reported as `undisclosed` (the total minus the
    disclosed industries), so shares can be taken of the real total."""
    if not lines:
        return {"total": None, "year": year, "by_industry": None, "undisclosed": None, "estimated": None}
    lines = fill_suppressed(lines)
    by = {k: lines.get(line) for k, line in GDP_LINES.items()}
    est = sorted(k for k, line in GDP_LINES.items() if estimated and line in estimated and by.get(k) is not None)
    total = lines.get("1")
    undisclosed = None
    if total is not None and any(v is None for v in by.values()):
        undisclosed = max(0, round(total - sum(v for v in by.values() if v is not None)))
    return {"total": total, "year": year, "by_industry": by, "undisclosed": undisclosed, "estimated": est or None}


def bea_combos(bea: dict, gaz_counties: dict) -> dict[str, list[str]]:
    """BEA combination areas → component county FIPS, by matching names to the Gazetteer.

    Virginia: 'Fairfax, Fairfax City + Falls Church, VA*' = Fairfax County + Fairfax city + Falls Church city.
    Hawaii: 'Maui + Kalawao, HI*' = Maui County + Kalawao County.
    """
    by_name = {(g["usps"], g["name"].lower()): geoid for geoid, g in gaz_counties.items()}
    out = {}
    for fips, name in bea["county_names"].items():
        if " + " not in name:
            continue
        body, _, st = name.rpartition(", ")
        st = st.rstrip("*")
        parts = re.split(r", | \+ ", body)
        names = [f"{parts[0]} County"] + [
            re.sub(r" City$", "", p) + (" city" if st == "VA" else " County") for p in parts[1:]
        ]
        comps = [by_name.get((st, n.lower())) for n in names]
        if None in comps:
            raise RuntimeError(f"cannot resolve BEA area {fips} {name!r} → {names}")
        out[fips] = comps
    return out


def sum_counties(fips_list: list[str], bea: dict, combos: dict, pop: dict, estimated: set | None = None) -> tuple[dict, bool]:
    """Add up BEA lines over counties. Returns ({line: usd|None}, split_used). Lines that needed an apportioned
    county value are added to `estimated`.

    Withheld county lines are first recovered from their parent and sibling lines (fill_suppressed: exactly where
    possible, else the parent's remainder split in the nation's proportions); a line is None if any county's value
    is still unknown. A combination area counts in full
    when all its parts are in the list, otherwise by the parts' share of population.
    """
    wanted = set(fips_list)
    part_of = {c: (combo, comps) for combo, comps in combos.items() for c in comps}
    shares: dict[str, float] = {}
    split = False
    for f in wanted:
        if f in bea["counties"]:
            shares[f] = 1.0
        elif f in part_of:
            combo, comps = part_of[f]
            if combo in shares:
                continue
            inside = [c for c in comps if c in wanted]
            if len(inside) == len(comps):
                shares[combo] = 1.0
            else:
                total = sum(pop.get(c) or 0 for c in comps)
                shares[combo] = sum(pop.get(c) or 0 for c in inside) / total if total else None
                split = True
        else:
            shares[f] = None  # not in BEA at all
    filled = {f: fill_suppressed(bea["counties"].get(f, {}), bea.get("nation"), estimated) for f in shares}
    out: dict[str, float | None] = {}
    for line in bea["lines"]:
        acc = 0.0
        for f, share in shares.items():
            v = filled[f].get(line)
            if v is None or share is None:
                acc = None
                break
            acc += v * share
        out[line] = round(acc) if acc is not None else None
    return out, split
