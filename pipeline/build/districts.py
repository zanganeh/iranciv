"""Districts: real industry specialisation (BLS QCEW) as strategy-game districts, per metro and per county.

QCEW stopped publishing metro areas by industry with the 2025 data, so a metro's jobs in an industry are the sum of
its member counties (cities.json `counties`). Counties whose row BLS withheld for confidentiality are left out of
the sum, so such a value is a lower bound (`suppressed` counts them, `lower_bound` flags it); a value with no
disclosed county at all is null, never 0.

Location quotient (LQ) = (metro jobs in the district's industries ÷ metro jobs in all industries) ÷ (the same
ratio for the nation). All industries' total is QCEW industry 10, all ownerships.

The rule (published in the file as `rule`):
* A district's score is the LQ of the sum of its industries (`naics`, ownerships `own`).
* It **appears** when that LQ is at least MIN_LQ and it has at least MIN_JOBS jobs or MIN_SHARE of the metro's
  jobs ("broad"), or when one of its listed specialities (`sub`) has an LQ of at least SPECIALTY_LQ with at least
  SPECIALTY_JOBS jobs or SPECIALTY_SHARE of the metro's jobs, and never under MIN_JOBS ("specialty": e.g. Houston's oil and gas inside a manufacturing-sized Industrial zone).
* Districts are ranked by strength = jobs above the national norm (emp - emp / LQ), taking the larger of the
  district's and its qualifying specialty's; up to MAX_PER_CITY are kept. (Ranking by jobs rather than by LQ
  stops a small, extreme speciality from outranking what most of a city does.) Harbor needs a footprint hex on or next to water, or on a mapped river.
* The city type `badge` comes from the strongest district, unless another listed district is extreme
  (LQ >= BADGE_LQ and >= BADGE_SHARE of the metro's jobs: Huntsville's defense civilians, Elizabethtown's Fort Knox),
  in which case the strongest such district wins. Its label is the speciality's nickname when one qualifies, else
  the district's generic nickname (one vocabulary: 'Factory town', 'Military town', ...). When BLS withholds the
  Campus jobs but R1 universities enrol at least COLLEGE_SHARE of the metro's population (IPEDS), the badge is
  'College town' (basis 'ipeds', flagged `withheld`). No district → no badge ("diversified").
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim, load_interim_payload, write_web

REQUIRES = ["bls_qcew", "ipeds", "_world", "cities.json", "world.json"]
PRODUCES = ["districts.json"]

MIN_LQ = 1.25
MIN_JOBS = 1000
MIN_SHARE = 0.01
SPECIALTY_LQ = 3.0
SPECIALTY_JOBS = 5000      # a speciality alone makes a district only when it is a real share of the metro:
SPECIALTY_SHARE = 0.005    # at least this many jobs, or this share of the metro's jobs
MAX_PER_CITY = 5
TOP_INDUSTRIES = 8
ALL_OWN = [1, 2, 3, 5]
BADGE_LQ = 6.0
BADGE_SHARE = 0.03
COLLEGE_SHARE = 0.15

RULE_TEXT = (
    f"A district appears when the jobs in its industries have a location quotient (the metro's share of jobs in "
    f"them divided by the national share) of at least {MIN_LQ:g} and number at least {MIN_JOBS:,} or "
    f"{MIN_SHARE:.0%} of the metro's jobs, or when one of its listed specialities has a location quotient of at "
    f"least {SPECIALTY_LQ:g} with at least {SPECIALTY_JOBS:,} jobs or {SPECIALTY_SHARE:.1%} of the metro's jobs (and "
    f"never fewer than {MIN_JOBS:,}). Districts are ranked by how many jobs they have "
    f"above the national norm (jobs minus jobs divided by the quotient); up to {MAX_PER_CITY} per city. A Harbor also needs a metro hex on or next to water or a mapped "
    f"river. Metro jobs add up the member counties (BLS publishes metros as totals only from 2025 on); counties "
    f"BLS withheld are left out, so such values are lower bounds. The city type is named after the strongest "
    f"district, or after a listed district with a quotient of at least {BADGE_LQ:g} and at least {BADGE_SHARE:.0%} of "
    f"the metro's jobs when there is one, and labelled by its speciality when one passes; 'Seat of government' and "
    f"'Capital city' go only to metros holding a state capital or the national capital. When BLS withholds the "
    f"Campus jobs but R1 research universities enrol students numbering at least {COLLEGE_SHARE:.0%} of the metro's "
    f"population (IPEDS 12-month headcount, which includes online students), the city type is 'College town'."
)


def _d(id, name, color, naics, own, sub, badge, labels, description, caveat=None):
    return {
        "id": id, "name": name, "icon": "d-" + id.replace("_", "-"), "color": color, "naics": naics, "own": own,
        "sub": sub, "badge": {"id": badge[0], "label": badge[1], "labels": labels},
        "description": description, "caveat": caveat,
    }


CATALOG = [
    _d("campus", "Campus", "#4a7bd0", ["5417", "6113", "5415", "5132", "5182"], ALL_OWN,
       {"5417": "Research labs", "6113": "Universities", "5132": "Software", "5415": "Computer services",
        "5182": "Data centres & hosting"},
       ("scientific", "Knowledge city"),
       {"5417": "Research city", "6113": "College town", "5132": "Tech city", "5415": "Tech city", "5182": "Tech city"},
       "Science and technology: research labs, colleges and universities, software publishers, computer "
       "services and data hosting."),
    _d("commercial_hub", "Commercial hub", "#d0a23a", ["52"], ALL_OWN,
       {"5231": "Securities & brokerage", "524": "Insurance"},
       ("commercial", "Finance city"), {"5231": "Finance capital", "524": "Insurance city"},
       "Finance and insurance: banks, securities and commodity trading, insurers."),
    _d("industrial_zone", "Industrial zone", "#c2643a", ["31_33", "211", "2121"], ALL_OWN,
       {"3361": "Motor vehicles", "3344": "Semiconductors", "3254": "Pharmaceuticals", "3241": "Oil refining",
        "331": "Primary metals", "211": "Oil & gas", "2121": "Coal mining"},
       ("industrial", "Factory town"),
       {"3361": "Motor city", "3344": "Silicon city", "3254": "Pharma city", "3241": "Refinery town",
        "331": "Steel town", "211": "Oil town", "2121": "Coal town"},
       "Manufacturing plus oil and gas extraction and coal mining (the strategy-game home of power plants)."),
    _d("harbor", "Harbor", "#2f8fb0", ["483", "4883"], ALL_OWN,
       {"483": "Shipping lines", "4883": "Port services"},
       ("maritime", "Port city"), {"483": "Port city", "4883": "Port city"},
       "Water transport: shipping and barge lines, and port and harbour services (cargo handling, pilots, "
       "marinas).", "Only for metros with a hex on or next to water or a mapped river."),
    _d("theater_square", "Theater square", "#a15bc4", ["711", "512", "516"], ALL_OWN,
       {"711": "Performing arts & sports", "512": "Film & recording", "516": "Broadcasting"},
       ("cultural", "Arts city"), {"711": "Arts city", "512": "Film capital", "516": "Media city"},
       "Culture and media: theatres, orchestras, spectator sports, film and music studios, broadcasters."),
    _d("entertainment_complex", "Entertainment complex", "#d0508a", ["713", "721"], ALL_OWN,
       {"7132": "Casinos", "713": "Amusement & recreation", "721": "Hotels & resorts"},
       ("entertainment", "Resort city"), {"7132": "Casino city", "713": "Resort city", "721": "Resort city"},
       "Leisure: theme parks, casinos, golf, ski and fitness, plus hotels and resorts. Casino hotels count as "
       "hotels (NAICS 721120)."),
    _d("aerodrome", "Aerodrome", "#6a8fb8", ["481", "4881", "3364"], ALL_OWN,
       {"481": "Airlines", "4881": "Airport services", "3364": "Aerospace"},
       ("aviation", "Air hub"), {"481": "Air hub", "4881": "Air hub", "3364": "Aerospace city"},
       "Aviation: airlines, airport services and aerospace manufacturing."),
    _d("government_plaza", "Government plaza", "#8a9a5b", ["921", "922", "923", "924", "925", "926", "927"], [1, 2],
       {"921": "Government offices", "922": "Courts, police & prisons", "923": "Benefit agencies",
        "926": "Economic agencies", "927": "Space agency"},
       ("governmental", "Government town"),
       {"921": "Government town", "922": "Government town", "923": "Government town", "926": "Government town",
        "927": "Space city"},
       "Federal and state public administration (NAICS 92 without national security). Local government offices "
       "are in every county and are left out.",
       "State universities and hospitals are not public administration (they count in Campus)."),
    _d("encampment", "Encampment", "#8a4a3a", ["928110"], [1, 2],
       {"928110": "Defense civilians"},
       ("militaristic", "Military town"), {"928110": "Military town"},
       "National security: civilian Department of Defense staff and state National Guard employees.",
       "Civilian defense staff only: uniformed troops are not in QCEW (see the Military layer)."),
    _d("aqueduct", "Aqueduct & dam", "#3aa0a0", ["22"], ALL_OWN,
       {"2211": "Power utilities", "2213": "Water & sewer"},
       ("utility", "Utility town"), {"2211": "Power town", "2213": "Waterworks town"},
       "Utilities: electric power generation and distribution, water, sewage and gas."),
]
BY_ID = {d["id"]: d for d in CATALOG}
IDS = [d["id"] for d in CATALOG]


class Metrics:
    """Adds up QCEW county entries for a set of counties."""

    def __init__(self, q: dict):
        self.q = q
        self.owns = q["owns"]
        us_emp, us_wages, _ = q["us_total"]
        self.us_emp, self.us_wages = us_emp, us_wages

    def total(self, fips: list[str]) -> dict:
        emp = wages = 0
        known = 0
        for f in fips:
            e = self.q["totals"].get(f)
            if e:
                emp += e[0]
                wages += e[1]
                known += 1
        return {"emp": emp if known else None, "wages": wages if known else None, "known": known}

    def industry(self, codes: list[str], own: list[int], fips: list[str]) -> dict:
        """{emp, wages, chg, suppressed} over counties; emp None when no county disclosed anything."""
        emp = wages = chg = 0
        suppressed = 0
        disclosed = 0
        for f in fips:
            sup = False
            for c in codes:
                row = self.q["counties"].get(c, {}).get(f)
                if row is None:
                    continue  # no establishment of any ownership: 0 jobs
                for o in own:
                    e = row[self.owns.index(o)]
                    if e is None:
                        sup = True
                    elif e:
                        emp += e[0]
                        wages += e[1]
                        chg += e[2]
            if sup:
                suppressed += 1
            else:
                disclosed += 1
        if disclosed == 0 and suppressed > 0 and emp == 0:
            return {"emp": None, "wages": None, "chg": None, "suppressed": suppressed}
        return {"emp": emp, "wages": wages, "chg": chg, "suppressed": suppressed}

    def us(self, codes: list[str], own: list[int]) -> int:
        n = 0
        for c in codes:
            for o in own:
                e = self.q["us"][c][self.owns.index(o)]
                if e:
                    n += e[0]
        return n


def _r(x, nd=3):
    return None if x is None else round(x, nd)


def lq(emp, total, us_emp, us_total):
    if emp is None or not total or not us_emp or not us_total:
        return None
    return (emp / total) / (us_emp / us_total)


def excess(emp, q) -> float:
    """Jobs above the national norm: emp - emp / LQ (0 when LQ <= 1)."""
    return emp * (1 - 1 / q) if emp and q and q > 1 else 0.0


def evaluate(m: Metrics, fips: list[str], total: int | None, wet: bool = True) -> list[dict]:
    """Every district that passes the rule for this set of counties, strongest first (max MAX_PER_CITY)."""
    out = []
    if not total:
        return out
    for d in CATALOG:
        if d["id"] == "harbor" and not wet:
            continue
        v = m.industry(d["naics"], d["own"], fips)
        if v["emp"] is None:
            continue
        dlq = lq(v["emp"], total, m.us(d["naics"], d["own"]), m.us_emp)
        share = v["emp"] / total
        broad = dlq is not None and dlq >= MIN_LQ and (v["emp"] >= MIN_JOBS or share >= MIN_SHARE)
        subs = []
        for code in d["sub"]:
            s = m.industry([code], d["own"], fips)
            if s["emp"] is None or s["emp"] < MIN_JOBS:
                continue
            slq = lq(s["emp"], total, m.us([code], d["own"]), m.us_emp)
            if slq is not None and slq > 1:
                subs.append((code, slq, s["emp"], excess(s["emp"], slq), s["suppressed"] > 0))
        special = [x for x in subs if x[1] >= SPECIALTY_LQ
                   and (x[2] >= SPECIALTY_JOBS or x[2] >= SPECIALTY_SHARE * total)]
        best_sub = max(special or subs, key=lambda x: x[3], default=None)
        specialty = bool(special)
        if not (broad or specialty):
            continue
        strength = max(excess(v["emp"], dlq) if broad else 0, best_sub[3] if specialty else 0)
        out.append({
            "id": d["id"], "lq": _r(dlq, 2), "emp": v["emp"], "share": _r(share, 4),
            "avg_pay": round(v["wages"] / v["emp"]) if v["emp"] else None,
            "sub": best_sub[0] if best_sub else None, "sub_lq": _r(best_sub[1], 2) if best_sub else None,
            "sub_emp": best_sub[2] if best_sub else None,
            "sub_lower_bound": bool(best_sub[4]) if best_sub else None,
            "via": "broad" if broad else "specialty", "strength": round(strength),
            "suppressed": v["suppressed"], "lower_bound": v["suppressed"] > 0,
        })
    out.sort(key=lambda r: -r["strength"])
    return out[:MAX_PER_CITY]


def badge_district(dists: list[dict]) -> dict | None:
    """The district the city type is named after: the strongest, unless a listed district is extreme."""
    if not dists:
        return None
    extreme = [d for d in dists if d["lq"] is not None and d["lq"] >= BADGE_LQ and (d["share"] or 0) >= BADGE_SHARE]
    return max(extreme, key=lambda d: d["strength"]) if extreme else dists[0]


def college_badge(univs: list[tuple[str, int]], pop, campus_withheld: bool) -> dict | None:
    """'College town' from IPEDS when BLS withholds the Campus jobs of a metro dominated by R1 universities."""
    if not campus_withheld or not univs or not pop:
        return None
    enrolled = sum(e for _, e in univs)
    if enrolled < COLLEGE_SHARE * pop:
        return None
    names = ", ".join(n for n, _ in sorted(univs, key=lambda t: -t[1])[:2])
    return {"id": "scientific", "label": "College town", "district": "campus", "basis": "ipeds", "withheld": True,
            "text": f"{names}: R1 research university enrolment equal to {enrolled / pop:.0%} of the metro's people "
                    f"(IPEDS, 12-month headcount). BLS withholds part of the Campus jobs here, so that district cannot be measured."}


# State capitals of the lower 48 (city name, state): 'Seat of government' goes only to metros holding one.
STATE_CAPITALS = [
    ("Montgomery", "AL"), ("Phoenix", "AZ"), ("Little Rock", "AR"), ("Sacramento", "CA"), ("Denver", "CO"),
    ("Hartford", "CT"), ("Dover", "DE"), ("Tallahassee", "FL"), ("Atlanta", "GA"), ("Boise City", "ID"),
    ("Springfield", "IL"), ("Indianapolis", "IN"), ("Des Moines", "IA"), ("Topeka", "KS"), ("Frankfort", "KY"),
    ("Baton Rouge", "LA"), ("Augusta", "ME"), ("Annapolis", "MD"), ("Boston", "MA"), ("Lansing", "MI"),
    ("St. Paul", "MN"), ("Jackson", "MS"), ("Jefferson City", "MO"), ("Helena", "MT"), ("Lincoln", "NE"),
    ("Carson City", "NV"), ("Concord", "NH"), ("Trenton", "NJ"), ("Santa Fe", "NM"), ("Albany", "NY"),
    ("Raleigh", "NC"), ("Bismarck", "ND"), ("Columbus", "OH"), ("Oklahoma City", "OK"), ("Salem", "OR"),
    ("Harrisburg", "PA"), ("Providence", "RI"), ("Columbia", "SC"), ("Pierre", "SD"), ("Nashville", "TN"),
    ("Austin", "TX"), ("Salt Lake City", "UT"), ("Montpelier", "VT"), ("Richmond", "VA"), ("Olympia", "WA"),
    ("Charleston", "WV"), ("Madison", "WI"), ("Cheyenne", "WY"),
    ("Barre", "VT"),  # Montpelier's micro area is named after Barre
]


def holds_state_capital(city: dict) -> bool:
    """True when one of the metro's named principal cities is a state capital in one of its states."""
    full = city.get("full") or city.get("name") or ""
    names, _, st = full.partition(", ")
    states = set(city.get("states") or []) | set(st.split(" ")[0].split("-"))
    parts = {p.strip() for p in names.split("-")}
    return any(cap in parts and abbr in states for cap, abbr in STATE_CAPITALS)


def badge_for(best: dict | None, capital: bool = False, state_capital: bool = False) -> dict | None:
    if not best:
        return None
    d = BY_ID[best["id"]]
    sub = best["sub"]
    use_sub = sub is not None and best["sub_lq"] is not None and best["sub_lq"] >= SPECIALTY_LQ
    label = d["badge"]["labels"].get(sub, d["badge"]["label"]) if use_sub else d["badge"]["label"]
    if capital and d["id"] == "government_plaza":
        label = "Capital city"
    elif d["id"] == "government_plaza" and use_sub and sub == "921" and state_capital:
        label = "Seat of government"
    if use_sub:
        ge = "≥" if best.get("sub_lower_bound") else ""
        text = f"{d['sub'][sub]}: {ge}{best['sub_lq']:.1f}× the national share of jobs"
    else:
        ge = "≥" if best.get("lower_bound") else ""
        text = f"{d['name']}: {ge}{best['lq']:.1f}× the national share of jobs"
    return {"id": d["badge"]["id"], "label": label, "district": d["id"], "text": text, "basis": "qcew"}


def wet_cities(cities: list[dict], world_i: dict, world: dict) -> set[str]:
    """Ids of cities with a footprint hex on or next to water, or on a mapped river."""
    grid = HexGrid(**world_i["grid"])
    water = world_i["water"]
    river_cells = set()
    for r in world.get("rivers") or []:
        pts = r.get("pts") or []
        for i in range(0, len(pts) - 2, 2):
            x0, y0, x1, y1 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
            n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / 4))
            for k in range(n + 1):
                c = grid.cell_at(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n)
                if c is not None and 0 <= c < len(water):
                    river_cells.add(c)
    out = set()
    for c in cities:
        cells = c.get("footprint") or ([c["cell"]] if c.get("cell") is not None else [])
        for cell in cells:
            if cell in river_cells or any(water[n] > 0 for n in [cell, *grid.neighbours(cell)]):
                out.add(c["id"])
                break
    return out


def build() -> None:
    payload = load_interim_payload("bls_qcew")
    q = payload["data"]
    world_i = load_interim("_world")
    cities = json.loads((WEB_DATA / "cities.json").read_text())
    world = json.loads((WEB_DATA / "world.json").read_text())
    m = Metrics(q)
    titles = q["codes"]
    wet = wet_cities(cities, world_i, world)
    r1 = {}
    for i in load_interim_payload("ipeds")["data"]["institutions"].values():
        if i.get("research") == 1 and i.get("cbsa") and i.get("enrollment"):
            r1.setdefault(i["cbsa"], []).append((i["name"].replace("-Main Campus", ""), i["enrollment"]))
    campus = BY_ID["campus"]

    detail = [c for c in titles if c not in ("10", "31_33", "52", "22")]
    industries = {}
    for c in titles:
        if c == "10":
            continue
        us_emp = m.us([c], ALL_OWN)
        industries[c] = {"title": titles[c], "us_emp": us_emp}

    out_cities: dict[str, dict] = {}
    for c in cities:
        fips = c.get("counties") or []
        tot = m.total(fips)
        if tot["emp"] is None:
            out_cities[c["id"]] = None
            continue
        total = tot["emp"]
        top = []
        for code in detail:
            v = m.industry([code], ALL_OWN, fips)
            if v["emp"] is None or v["emp"] < MIN_JOBS:
                continue
            ilq = lq(v["emp"], total, industries[code]["us_emp"], m.us_emp)
            if ilq is None:
                continue
            prev = v["emp"] - v["chg"]
            top.append({
                "naics": code, "emp": v["emp"], "lq": _r(ilq, 2),
                "avg_pay": round(v["wages"] / v["emp"]) if v["emp"] else None,
                "oty_pct": _r(v["chg"] / prev, 4) if prev > 0 else None,
                "suppressed": v["suppressed"], "lower_bound": v["suppressed"] > 0,
            })
        top.sort(key=lambda r: -r["lq"])
        dists = evaluate(m, fips, total, c["id"] in wet)
        out_cities[c["id"]] = {
            "emp": total, "avg_pay": round(tot["wages"] / total) if total else None,
            "counties": [tot["known"], len(fips)],
            "top": top[:TOP_INDUSTRIES],
            "districts": dists,
            "badge": None,
        }
        withheld = (not any(d["id"] == "campus" for d in dists)
                    and m.industry(campus["naics"], campus["own"], fips)["suppressed"] > 0)
        out_cities[c["id"]]["badge"] = (college_badge(r1.get(c["id"], []), c.get("population"), withheld)
                                        or badge_for(badge_district(dists), bool(c.get("capital")),
                                                     holds_state_capital(c)))

    out_counties = {}
    for f, t in sorted(q["totals"].items()):
        if not t:
            continue
        dists = evaluate(m, [f], t[0], True)
        out_counties[f] = {"emp": t[0], "districts": [[d["id"], d["lq"], d["emp"]] for d in dists]}

    year = q["year"]
    catalog = []
    for d in CATALOG:
        e = dict(d)
        e["naics"] = list(d["naics"])
        catalog.append(e)
    doc = {
        "version": 1,
        "vintage": payload.get("vintage") or q["vintage"],
        "year": year,
        "sources": ["bls_qcew", "ipeds"],
        "rule": {"min_lq": MIN_LQ, "min_jobs": MIN_JOBS, "min_share": MIN_SHARE, "specialty_lq": SPECIALTY_LQ,
                 "specialty_jobs": SPECIALTY_JOBS, "specialty_share": SPECIALTY_SHARE,
                 "max_per_city": MAX_PER_CITY, "harbor_needs_water": True, "badge_lq": BADGE_LQ,
                 "badge_share": BADGE_SHARE, "college_share": COLLEGE_SHARE, "text": RULE_TEXT,
                 "county_note": "County districts use the same rule without the Harbor water test."},
        "catalog": catalog,
        "industries": industries,
        "national": {"emp": m.us_emp, "avg_pay": round(m.us_wages / m.us_emp)},
        "cities": out_cities,
        "counties": out_counties,
    }
    write_web("districts.json", doc)


# ---------------------------------------------------------------------------------------------- check


def check(web_dir: Path) -> list[tuple[str, str]]:
    web_dir = Path(web_dir)
    p = web_dir / "districts.json"
    if not p.exists():
        return [("NOTE", "districts.json: missing (optional, skipped)")]
    out: list[tuple[str, str]] = []
    bad = lambda m: out.append(("FAIL", f"districts.json: {m}"))  # noqa: E731
    try:
        doc = json.loads(p.read_text())
    except ValueError as exc:
        return [("FAIL", f"districts.json: invalid JSON ({exc})")]
    if not isinstance(doc, dict):
        return [("FAIL", "districts.json: top level is not an object")]
    for k in ("version", "vintage", "year", "sources", "rule", "catalog", "industries", "national", "cities", "counties"):
        if k not in doc:
            bad(f"missing key {k!r}")
    if out:
        return out
    sp = web_dir / "sources.json"
    if sp.exists() and isinstance(doc["sources"], list):
        try:
            known = {x.get("id") for x in json.loads(sp.read_text()) if isinstance(x, dict)}
        except (ValueError, AttributeError):
            known = None
        if known is not None and set(doc["sources"]) - known:
            out.append(("WARN", f"districts.json: sources missing from sources.json: {sorted(set(doc['sources']) - known)}"))
    if doc["version"] != 1:
        bad(f"version {doc['version']!r} != 1")
    if not isinstance(doc["sources"], list) or not doc["sources"]:
        bad("sources must be a non-empty list")
    if not isinstance(doc["rule"], dict) or not isinstance(doc["rule"].get("text"), str):
        bad("rule.text missing")
    cat = doc["catalog"]
    ids = [d.get("id") for d in cat] if isinstance(cat, list) else []
    if ids != IDS:
        bad(f"catalog ids {ids} != {IDS}")
    for d in cat if isinstance(cat, list) else []:
        for k in ("name", "icon", "color", "naics", "own", "sub", "badge", "description"):
            if k not in d:
                bad(f"catalog {d.get('id')}: missing {k!r}")
    industries = doc["industries"] if isinstance(doc["industries"], dict) else {}
    cities_path = web_dir / "cities.json"
    city_ids = None
    if cities_path.exists():
        try:
            city_ids = {c.get("id") for c in json.loads(cities_path.read_text()) if isinstance(c, dict)}
        except (ValueError, TypeError, AttributeError):
            city_ids = None
    cities = doc["cities"] if isinstance(doc["cities"], dict) else {}
    if city_ids is not None:
        missing = city_ids - set(cities)
        extra = set(cities) - city_ids
        if missing:
            bad(f"{len(missing)} cities.json ids missing, e.g. {sorted(missing)[:3]}")
        if extra:
            bad(f"{len(extra)} ids not in cities.json, e.g. {sorted(extra)[:3]}")
    n_null = n_badge = n_lower = 0
    counts = {i: 0 for i in IDS}
    for cid, row in cities.items():
        if row is None:
            n_null += 1
            continue
        if not isinstance(row.get("emp"), (int, float)) or row["emp"] <= 0:
            bad(f"city {cid}: emp must be a positive number")
        dl = row.get("districts")
        if not isinstance(dl, list) or len(dl) > MAX_PER_CITY:
            bad(f"city {cid}: districts must be a list of at most {MAX_PER_CITY}")
            continue
        for d in dl:
            if d.get("id") not in counts:
                bad(f"city {cid}: unknown district {d.get('id')!r}")
                continue
            counts[d["id"]] += 1
            if d.get("emp") is None or d["emp"] <= 0:
                bad(f"city {cid}: district {d['id']} without jobs")
            if d.get("lower_bound") != (d.get("suppressed", 0) > 0):
                bad(f"city {cid}: district {d['id']} lower_bound disagrees with suppressed")
            if d.get("lower_bound"):
                n_lower += 1
        for t in row.get("top") or []:
            if t.get("naics") not in industries:
                bad(f"city {cid}: top industry {t.get('naics')!r} not in industries")
        b = row.get("badge")
        if b is not None:
            n_badge += 1
            if b.get("basis") == "ipeds":
                if b.get("withheld") is not True or b.get("district") != "campus":
                    bad(f"city {cid}: an IPEDS badge must be a withheld Campus")
            elif not dl or b.get("district") not in {d.get("id") for d in dl}:
                bad(f"city {cid}: badge does not name one of its districts")
        elif dl:
            bad(f"city {cid}: districts but no badge")
    if any(lv == "FAIL" for lv, _ in out):
        return out
    out.append(("PASS", f"districts.json: {len(cities)} cities ({n_null} without QCEW data), {n_badge} with a city "
                        f"type, {len(doc['counties'])} counties"))
    out.append(("NOTE", "districts.json: cities per district: " + ", ".join(f"{k} {v}" for k, v in counts.items())))
    out.append(("NOTE", f"districts.json: {n_lower} city districts are lower bounds (suppressed counties left out)"))
    return out
