"""Citizens: each metro's mix of people for the City View's person icons, from ACS 1-year tables (acs_1yr).

Five rows per metro: workers by occupation (C24010), people by age (B01001, median age B01002), how workers get to
work (C08301), adults 25+ by education (C15003) and people by place of birth (B05002). The ACS interim keeps male and
female lines apart; this builder adds them (female line = male line + 36 in C24010, + 24 in B01001).

Icon counts (`figures`) are computed here with largest-remainder rounding, so each row's figures add up exactly to
its icon count and the frontend only draws them. Census leaves some small areas out of a table entirely: that row
is then null (never zeros), and a city missing from every table is null.

`specialists` lists the categories whose share is at least SPECIALIST_HIGH or at most SPECIALIST_LOW times the
national share (up to MAX_HIGH over-represented, strongest first, then up to MAX_LOW under-represented; categories
under MIN_SHARE in both the city and the nation are skipped as noise). `reliable` is false for micropolitan areas, whose 1-year margins of error are large.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..core.paths import WEB_DATA
from ..core.store import load_interim, write_web

REQUIRES = ["acs_1yr", "cities.json"]
PRODUCES = ["citizens.json"]

FIGURES = {"workforce": 20, "age": 10, "commute": 10}
SPECIALIST_HIGH = 1.5
SPECIALIST_LOW = 0.67
MIN_SHARE = 0.01
MAX_HIGH, MAX_LOW = 4, 2  # specialists kept: the strongest over- and under-represented categories
NATION = "0100000US"


def _lines(table: str, plus: list[int], minus: list[int] = (), female: int | None = None) -> dict:
    return {"table": table, "plus": plus, "minus": list(minus), "female": female}


def _g(key, label, icon, spec):
    """A category: `spec` = which estimate lines it adds (and subtracts); female lines are male + `female`."""
    t, plus, minus, fem = spec["table"], spec["plus"], spec["minus"], spec["female"]
    txt = " + ".join(f"{l:03d}" for l in plus) + "".join(f" − {l:03d}" for l in minus)
    if fem:
        txt += f" (male lines; female = male + {fem})"
    return {"key": key, "label": label, "icon": icon, "lines": f"{t} {txt}", "_spec": spec}


def _r(a, b):
    return list(range(a, b + 1))


C24, B01 = "C24010", "B01001"
GROUPS = {
    "workforce": [
        _g("executive", "Executives & managers", "c-executive", _lines(C24, [4], female=36)),
        _g("engineer", "Engineers, coders & scientists", "c-engineer", _lines(C24, [7], female=36)),
        _g("scholar", "Teachers, lawyers, artists & media", "c-scholar", _lines(C24, [11], female=36)),
        _g("healer", "Health workers", "c-healer", _lines(C24, [16, 20], female=36)),
        _g("service", "Service workers (food, cleaning, care, protection)", "c-service",
           _lines(C24, [19], [20], female=36)),
        _g("clerk", "Sales & office workers", "c-clerk", _lines(C24, [27], female=36)),
        _g("farmer", "Farmers, fishers & foresters", "c-farmer", _lines(C24, [31], female=36)),
        _g("builder", "Builders & repairers", "c-builder", _lines(C24, [30], [31], female=36)),
        _g("maker", "Factory, transport & warehouse workers", "c-maker", _lines(C24, [34], female=36)),
    ],
    "age": [
        _g("child", "Children (under 18)", "c-child", _lines(B01, _r(3, 6), female=24)),
        _g("young", "Young adults (18-34)", "c-young", _lines(B01, _r(7, 12), female=24)),
        _g("midlife", "Middle-aged (35-64)", "c-midlife", _lines(B01, _r(13, 19), female=24)),
        _g("elder", "Elders (65+)", "c-elder", _lines(B01, _r(20, 25), female=24)),
    ],
    "commute": [
        _g("drove_alone", "Drive alone", "c-car", _lines("C08301", [3])),
        _g("carpool", "Carpool", "c-carpool", _lines("C08301", [4])),
        _g("transit", "Public transit", "c-transit", _lines("C08301", [8])),
        _g("walk", "Walk", "c-walk", _lines("C08301", [9])),
        _g("other", "Bike, taxi, motorcycle & other", "c-bike", _lines("C08301", [10])),
        _g("home", "Work from home", "c-home", _lines("C08301", [11])),
    ],
    "education": [
        _g("less_than_hs", "No high-school diploma", "c-edu0", _lines("C15003", _r(2, 9))),
        _g("high_school", "High school or GED", "c-edu1", _lines("C15003", [10, 11])),
        _g("some_college", "Some college or associate's", "c-edu2", _lines("C15003", [12, 13, 14])),
        _g("bachelors", "Bachelor's degree", "c-edu3", _lines("C15003", [15])),
        _g("graduate", "Master's, professional or doctorate", "c-edu4", _lines("C15003", [16, 17, 18])),
    ],
    "nativity": [
        _g("born_in_state", "Born in this state", "c-local", _lines("B05002", [3])),
        _g("other_state", "Born in another state", "c-newcomer", _lines("B05002", [4])),
        _g("native_abroad", "Born a U.S. citizen abroad or in a territory", "c-abroad", _lines("B05002", [2], [3, 4])),
        _g("foreign_born", "Born in another country", "c-immigrant", _lines("B05002", [13])),
    ],
}
TOTALS = {"workforce": "C24010_001", "age": "B01001_001", "commute": "C08301_001", "education": "C15003_001",
          "nativity": "B05002_001"}
ROW_LABELS = {
    "workforce": "Employed civilians 16+ by occupation",
    "age": "Residents by age",
    "commute": "Workers 16+ by how they get to work",
    "education": "Adults 25+ by education",
    "nativity": "Residents by place of birth",
}


def largest_remainder(values: dict[str, float], n: int) -> dict[str, int]:
    """Integer counts summing exactly to n, proportional to `values` (Hamilton's method).

    Ties in the remainders go to the larger value, then to the earlier key, so the result is deterministic.
    Negative values count as 0. All-zero input → all zeros.
    """
    keys = list(values)
    vals = [max(0.0, float(values[k] or 0)) for k in keys]
    total = sum(vals)
    if total <= 0 or n <= 0:
        return {k: 0 for k in keys}
    quotas = [v * n / total for v in vals]
    base = [math.floor(q) for q in quotas]
    left = n - sum(base)
    order = sorted(range(len(keys)), key=lambda i: (-(quotas[i] - base[i]), -vals[i], i))
    for i in order[:left]:
        base[i] += 1
    return dict(zip(keys, base))


def _value(rec: dict, spec: dict) -> float | None:
    """Sum of a category's lines (male + female when `female`), or None if any line is missing."""
    total = 0.0
    for sign, lines in ((1, spec["plus"]), (-1, spec["minus"])):
        for l in lines:
            for off in ([0, spec["female"]] if spec["female"] else [0]):
                v = rec.get(f"{spec['table']}_{l + off:03d}")
                if v is None:
                    return None
                total += sign * v
    return max(total, 0.0)


def row(rec: dict | None, name: str) -> dict | None:
    if not rec:
        return None
    total = rec.get(TOTALS[name])
    if not total or total <= 0:
        return None
    counts = {}
    for g in GROUPS[name]:
        v = _value(rec, g["_spec"])
        if v is None:
            return None
        counts[g["key"]] = round(v)
    out = {"total": round(total), "counts": counts,
           "shares": {k: round(v / total, 3) for k, v in counts.items()}}
    if name in FIGURES:
        out["figures"] = largest_remainder(counts, FIGURES[name])
    if name == "age":
        med = rec.get("B01002_001")
        out["median"] = round(med, 1) if med is not None else None
    if name == "education":
        out["bachelors_plus"] = round((counts["bachelors"] + counts["graduate"]) / total, 4)
    if name == "nativity":
        out["foreign_born"] = out["shares"]["foreign_born"]
        out["born_in_state"] = out["shares"]["born_in_state"]
    return out


def specialists(r: dict, nat: dict) -> list[dict]:
    hi, lo = [], []
    for name in GROUPS:
        a, b = r.get(name), nat.get(name)
        if not a or not b:
            continue
        for k, n in a["counts"].items():
            s, ns = n / a["total"], b["counts"][k] / b["total"]
            if not ns or (s < MIN_SHARE and ns < MIN_SHARE):
                continue
            ratio = s / ns
            item = {"row": name, "key": k, "share": round(s, 3), "national": round(ns, 3), "ratio": round(ratio, 2)}
            if ratio >= SPECIALIST_HIGH:
                hi.append(item)
            elif ratio <= SPECIALIST_LOW:
                lo.append(item)
    hi.sort(key=lambda x: -x["ratio"])
    lo.sort(key=lambda x: x["ratio"])
    return hi[:MAX_HIGH] + lo[:MAX_LOW]


def full_row(rec: dict | None, nat: dict | None, reliable: bool) -> dict | None:
    parts = {name: row(rec, name) for name in GROUPS}
    if all(v is None for v in parts.values()):
        return None
    if nat is not None:
        parts["specialists"] = specialists(parts, nat)
    parts["reliable"] = reliable
    return parts


def build() -> None:
    acs = load_interim("acs_1yr")
    cities = json.loads((WEB_DATA / "cities.json").read_text())
    nat_rec = acs["geos"].get(NATION)
    national = full_row(nat_rec, None, True)
    if national is None:
        raise ValueError("acs_1yr has no national row for the citizen tables (run: pipeline fetch acs_1yr)")
    national.pop("reliable", None)
    national["specialists"] = []
    out = {}
    for c in cities:
        out[c["id"]] = full_row(acs["cbsa"].get(c["id"]), national, bool(c.get("metro")))
    groups = {name: [{k: v for k, v in g.items() if k != "_spec"} for g in gs] for name, gs in GROUPS.items()}
    year = acs.get("year")
    doc = {
        "version": 1,
        "vintage": acs.get("vintage") or f"ACS {year} 1-year",
        "year": year,
        "sources": ["acs_1yr"],
        "figures": dict(FIGURES),
        "rows": dict(ROW_LABELS),
        "groups": groups,
        "rule": {
            "specialist_high": SPECIALIST_HIGH, "specialist_low": SPECIALIST_LOW, "min_share": MIN_SHARE,
            "max_high": MAX_HIGH, "max_low": MAX_LOW,
            "text": f"Figures are rounded with the largest-remainder method so each row adds up exactly. A "
                    f"specialist is a category whose share is at least {SPECIALIST_HIGH:g}× or at most "
                    f"{SPECIALIST_LOW:g}× the national share (categories under {MIN_SHARE:.0%} both here and "
                    f"nationally are skipped); up to {MAX_HIGH} over- and {MAX_LOW} under-represented are listed. Micropolitan areas are marked unreliable: their 1-year margins "
                    f"of error are large. Commute shares are of workers, not of all residents.",
        },
        "national": national,
        "cities": out,
    }
    write_web("citizens.json", doc)


# ---------------------------------------------------------------------------------------------- check


def _check_row(cid: str, r: dict, bad) -> None:
    for name in GROUPS:
        if name not in r:
            bad(f"{cid}: missing row {name!r}")
            continue
        v = r[name]
        if v is None:
            continue
        keys = [g["key"] for g in GROUPS[name]]
        if list(v.get("counts", {})) != keys or list(v.get("shares", {})) != keys:
            bad(f"{cid}: {name} categories differ from groups")
            continue
        if any(x is None or x < 0 for x in v["counts"].values()):
            bad(f"{cid}: {name} has negative or null counts")
        if abs(sum(v["counts"].values()) - v["total"]) > max(2, 0.001 * v["total"]):
            bad(f"{cid}: {name} counts sum {sum(v['counts'].values())} != total {v['total']}")
        if name in FIGURES:
            f = v.get("figures") or {}
            if list(f) != keys or sum(f.values()) != FIGURES[name]:
                bad(f"{cid}: {name} figures must cover every category and sum to {FIGURES[name]}")


def check(web_dir: Path) -> list[tuple[str, str]]:
    web_dir = Path(web_dir)
    p = web_dir / "citizens.json"
    if not p.exists():
        return [("NOTE", "citizens.json: missing (optional, skipped)")]
    out: list[tuple[str, str]] = []
    bad = lambda m: out.append(("FAIL", f"citizens.json: {m}"))  # noqa: E731
    try:
        doc = json.loads(p.read_text())
    except ValueError as exc:
        return [("FAIL", f"citizens.json: invalid JSON ({exc})")]
    if not isinstance(doc, dict):
        return [("FAIL", "citizens.json: top level is not an object")]
    for k in ("version", "vintage", "year", "sources", "figures", "groups", "national", "cities"):
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
            out.append(("WARN", f"citizens.json: sources missing from sources.json: {sorted(set(doc['sources']) - known)}"))
    if doc["version"] != 1:
        bad(f"version {doc['version']!r} != 1")
    if doc["figures"] != FIGURES:
        bad(f"figures {doc['figures']} != {FIGURES}")
    for name, gs in GROUPS.items():
        got = [g.get("key") for g in doc["groups"].get(name, [])]
        if got != [g["key"] for g in gs]:
            bad(f"groups.{name} keys {got} differ from the contract")
        for g in doc["groups"].get(name, []):
            if not g.get("label") or not g.get("lines"):
                bad(f"groups.{name}.{g.get('key')}: label and lines are required")
    if not isinstance(doc["national"], dict):
        bad("national row missing")
    else:
        _check_row("national", doc["national"], bad)
    cities = doc["cities"] if isinstance(doc["cities"], dict) else {}
    cp = web_dir / "cities.json"
    if cp.exists():
        try:
            ids = {c.get("id") for c in json.loads(cp.read_text()) if isinstance(c, dict)}
        except (ValueError, TypeError, AttributeError):
            ids = None
        if ids is not None:
            if ids - set(cities):
                bad(f"{len(ids - set(cities))} cities.json ids missing, e.g. {sorted(ids - set(cities))[:3]}")
            if set(cities) - ids:
                bad(f"{len(set(cities) - ids)} ids not in cities.json")
    n_null = 0
    row_null = {name: 0 for name in GROUPS}
    for cid, r in cities.items():
        if r is None:
            n_null += 1
            continue
        _check_row(cid, r, bad)
        for name in GROUPS:
            if r.get(name) is None:
                row_null[name] += 1
        if not isinstance(r.get("reliable"), bool):
            bad(f"{cid}: reliable must be a boolean")
    if any(lv == "FAIL" for lv, _ in out[:50]):
        return out[:50]
    out.append(("PASS", f"citizens.json: {len(cities)} cities, {n_null} with no ACS 1-year citizen tables"))
    out.append(("NOTE", "citizens.json: cities without a row (Census left them out): "
                + ", ".join(f"{k} {v}" for k, v in row_null.items())))
    return out
