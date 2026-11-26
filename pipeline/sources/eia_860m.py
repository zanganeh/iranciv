"""EIA-860M: every operating US generator, aggregated to power plants."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked, num, xlsx_records

BASE = "https://www.eia.gov/electricity/data/eia860m/"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
LINK = re.compile(r"((?:archive/)?xls/([a-z]+)_generator(\d{4})\.xlsx)", re.I)
FILE = re.compile(r"([a-z]+)_generator(\d{4})\.xlsx$")

# EIA energy source codes → map fuel categories.
FUEL = {
    "nuclear": {"NUC"},
    "coal": {"ANT", "BIT", "LIG", "SUB", "SGC", "WC", "RC"},
    "gas": {"NG", "BFG", "OG", "PG", "SG", "SGP"},
    "oil": {"DFO", "RFO", "JF", "KER", "PC", "WO"},
    "hydro": {"WAT"},
    "wind": {"WND"},
    "solar": {"SUN"},
    "battery": {"MWH"},
    "geothermal": {"GEO"},
    "bio": {"WDS", "WDL", "BLQ", "AB", "MSW", "OBS", "OBL", "OBG", "LFG"},
}
CATEGORY = {code: cat for cat, codes in FUEL.items() for code in codes}
# Status codes counted as capacity: (OP) operating and (SB) standby/backup. (OS) out of service for good and
# (OA) out of service but expected back next year are not; OA is kept per plant as idle_mw for the tooltip.
IN_SERVICE = {"OP", "SB"}
KEEP_MW = 50  # net summer MW; plants smaller than this are dropped from the interim (no map icon is that small)


def fuel_category(code: str, prime_mover: str) -> str:
    if code == "WAT" and prime_mover == "PS":
        return "pumped_storage"  # pumped-storage hydro is storage, not a river dam's generation
    return CATEGORY.get(code, "other")


class EIA860M(Source):
    info = SourceInfo(
        id="eia_860m",
        title="Preliminary Monthly Electric Generator Inventory (EIA-860M)",
        publisher="U.S. Energy Information Administration",
        homepage=BASE,
        license="Public domain (U.S. Government work)",
        description="Every US generator of 1 MW or more in EIA's 'Operating' sheet, with capacity, fuel, status and "
        "coordinates. Generators in service (operating or standby) are summed into power plants using net summer "
        "capacity (nameplate where that is blank); units out of service are left out, except that a plant's "
        "temporarily idle capacity (status OA) is kept as a separate figure.",
        used_for=["Power-plant resources (nuclear, hydro, wind, solar, coal, gas, geothermal, storage)"],
    )

    def _candidates(self) -> list[tuple[int, int, str]]:
        """(year, month, relative url) newest first: links on the landing page, then month-by-month guesses."""
        found = {}
        page = download_checked(self, BASE, "index.html", force=True, skip_errors=True)
        if page:
            for rel, month, year in LINK.findall(page.read_text(errors="replace")):
                if month.lower() in MONTHS:
                    key = (int(year), MONTHS.index(month.lower()) + 1)
                    # prefer the non-archive link when the page lists both
                    if key not in found or found[key].startswith("archive/"):
                        found[key] = rel
        today = date.today()
        y, m = today.year, today.month
        for _ in range(12):
            name = f"{MONTHS[m - 1]}_generator{y}.xlsx"
            found.setdefault((y, m), f"xls/{name}")
            m, y = (m - 1, y) if m > 1 else (12, y - 1)
        return [(y, m, rel) for (y, m), rel in sorted(found.items(), reverse=True)]

    def _cached(self):
        files = []
        for p in self.raw_dir.glob("*_generator*.xlsx"):
            if m := FILE.search(p.name.lower()):
                if m.group(1) in MONTHS:
                    files.append((int(m.group(2)), MONTHS.index(m.group(1)) + 1, p))
        return max(files) if files else None

    def fetch(self, force=False):
        if self._cached() and not force:
            return
        for year, month, rel in self._candidates():
            name = rel.rsplit("/", 1)[-1]
            urls = [BASE + rel] + ([BASE + "archive/" + rel] if not rel.startswith("archive/") else [])
            for url in urls:
                # skip_errors: a candidate that keeps failing moves us on to the next month
                if download_checked(self, url, name, force=force, magic=b"PK", binary=True, skip_errors=True):
                    return
        raise RuntimeError("no EIA-860M generator workbook could be downloaded")

    def process(self):
        cached = self._cached()
        if not cached:
            raise FileNotFoundError("no EIA-860M workbook – run: uv run python -m pipeline fetch eia_860m")
        year, month, path = cached
        plants: dict[str, dict] = {}
        mw_by = defaultdict(lambda: defaultdict(float))
        gens = defaultdict(lambda: defaultdict(int))
        years = defaultdict(lambda: defaultdict(list))
        entity_mw = defaultdict(lambda: defaultdict(float))
        nameplate_mw = defaultdict(float)
        idle_mw = defaultdict(float)  # (OA) out of service, expected back next year
        skipped = defaultdict(float)
        for r in xlsx_records(path, "Plant ID", sheet="Operating"):
            pid = r.get("Plant ID")
            lat, lon = num(r.get("Latitude")), num(r.get("Longitude"))
            nameplate = num(r.get("Nameplate Capacity (MW)"))
            # Net summer capacity is what EIA reports as a plant's capacity; nameplate is the fallback.
            mw = num(r.get("Net Summer Capacity (MW)"))
            mw = nameplate if mw is None else mw
            if not pid or lat is None or lon is None or mw is None:
                continue  # the final NOTES row and generators without coordinates
            status = r.get("Status", "")[1:3]  # '(OP) Operating' → 'OP'
            if status not in IN_SERVICE:
                skipped[status] += mw
                if status == "OA":
                    idle_mw[pid] += mw
                continue
            cat = fuel_category(r.get("Energy Source Code", ""), r.get("Prime Mover Code", ""))
            p = plants.setdefault(pid, {
                "id": int(pid), "name": r["Plant Name"], "state": r["Plant State"], "county": r["County"],
                "lat": round(lat, 5), "lon": round(lon, 5),
            })
            mw_by[pid][cat] += mw
            if r.get("Prime Mover Code") == "WS":
                p["offshore"] = True  # wind turbine, offshore
            nameplate_mw[pid] += nameplate or 0
            gens[pid][cat] += 1
            entity_mw[pid][r.get("Entity Name", "")] += mw
            if (yr := num(r.get("Operating Year"))) is not None:
                years[pid][cat].append(int(yr))
        if not plants:
            raise ValueError(f"no in-service generators in {path.name} – has the 'Status' column changed?")

        out = []
        for pid, p in plants.items():
            total = sum(mw_by[pid].values())
            if total < KEEP_MW and "geothermal" not in mw_by[pid]:
                continue
            p["operator"] = max(entity_mw[pid].items(), key=lambda kv: kv[1])[0]
            p["mw"] = round(total, 1)
            p["nameplate_mw"] = round(nameplate_mw[pid], 1)
            p["idle_mw"] = round(idle_mw[pid], 1)
            p["fuels"] = {
                cat: {"mw": round(v, 1), "generators": gens[pid][cat],
                      "years": [min(years[pid][cat]), max(years[pid][cat])] if years[pid][cat] else None}
                for cat, v in sorted(mw_by[pid].items(), key=lambda kv: -kv[1])
            }
            out.append(p)
        out.sort(key=lambda p: -p["mw"])
        label = f"{MONTHS[month - 1].capitalize()} {year}"
        print("    left out (not in service): " + ", ".join(f"{s or '?'} {v:,.0f} MW" for s, v in sorted(skipped.items())))
        print(f"    {len(plants)} plants, {len(out)} kept (>= {KEEP_MW} MW or geothermal), inventory {label}")
        return {
            "vintage": f"EIA-860M, {label}",
            "_files": [path.name],
            "as_of": f"{year}-{month:02d}",
            "plants": out,
        }
