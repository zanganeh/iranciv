"""NOAA NCEI U.S. Billion-Dollar Weather and Climate Disasters, 1980–2024 (final release).

Three small CSVs: CPI-adjusted cost by state and hazard (1980–2024 summed), event counts by year,
state and hazard, and the national list of events. Each CSV has one or two title lines above its
header. NCEI stopped updating the product after the 2024 data, so it is labelled "final release".
"""

from __future__ import annotations

import csv
import io

from ..core.source import Source, SourceInfo

BASE = "https://www.ncei.noaa.gov/access/billions/"
FILES = {
    "cost": "state-cost-data.csv",
    "freq": "state-freq-data.csv",
    "events": "events-US-1980-2024.csv",
}
FIRST_YEAR, LAST_YEAR = 1980, 2024
HAZARDS = {  # CSV column → id
    "drought": "drought", "flooding": "flooding", "freeze": "freeze", "severe storm": "severe_storm",
    "tropical cyclone": "tropical_cyclone", "wildfire": "wildfire", "winter storm": "winter_storm",
}


def _table(text: str, first: str) -> list[dict]:
    """Rows of a CSV whose header row starts with `first` (title lines above it are skipped)."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().lower().startswith(first))
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class NoaaBillionDollarDisasters(Source):
    info = SourceInfo(
        id="noaa_billions",
        title="U.S. Billion-Dollar Weather and Climate Disasters, 1980–2024",
        publisher="NOAA National Centers for Environmental Information (NCEI)",
        homepage="https://www.ncei.noaa.gov/access/billions/",
        license="Public domain (U.S. Government work); cite NOAA NCEI",
        description="Weather and climate disasters that each cost the U.S. at least $1 billion (CPI-adjusted), "
        "1980–2024, by state and hazard. Final release: NCEI ended the series after 2024. Costs are estimates "
        "with wide uncertainty and are given per state; one event usually touches many states, so state "
        "figures must not be added up into a national total (the national figures come from the event list).",
        used_for=["disasters.json (billion-dollar disasters by state)", "Disasters advisor"],
    )

    def fetch(self, force=False):
        for name in FILES.values():
            path = self.download(BASE + name, name, force=force)
            head = path.read_text(encoding="utf-8", errors="replace")[:400].lower()
            if "<html" in head or "billion" not in head:
                self.discard(name)
                raise RuntimeError(f"{BASE + name} did not return the expected CSV")

    def process(self):
        raw = {k: (self.raw_dir / n).read_text(encoding="utf-8") for k, n in FILES.items()}
        states: dict[str, dict] = {}
        for row in _table(raw["cost"], "state"):
            st = (row.get("state") or "").strip()
            if len(st) != 2:
                continue
            states.setdefault(st, {})["cost_usd"] = {
                hid: (round(_f(row.get(col)) * 1e6) if _f(row.get(col)) is not None else None) for col, hid in HAZARDS.items()
            }
        years = set()
        for row in _table(raw["freq"], "year"):
            st, yr = (row.get("state") or "").strip(), int(row["year"])
            years.add(yr)
            cnt = states.setdefault(st, {}).setdefault("count", {h: 0 for h in HAZARDS.values()})
            for col, hid in HAZARDS.items():
                v = _f(row.get(col))
                if v is not None:
                    cnt[hid] += int(v)
        if min(years) != FIRST_YEAR or max(years) != LAST_YEAR:
            raise RuntimeError(f"{FILES['freq']}: years {min(years)}–{max(years)}, expected {FIRST_YEAR}–{LAST_YEAR}")
        events = []
        for row in _table(raw["events"], "name"):
            begin = (row.get("Begin Date") or "").strip()
            cost = _f(row.get("CPI-Adjusted Cost"))
            if len(begin) != 8 or cost is None:
                continue
            events.append({
                "name": row["Name"].strip(), "type": row["Disaster"].strip(), "begin": f"{begin[:4]}-{begin[4:6]}-{begin[6:]}",
                "cost_usd": round(cost * 1e6), "deaths": int(_f(row.get("Deaths")) or 0) if _f(row.get("Deaths")) is not None else None,
            })
        if len(events) < 300:
            raise RuntimeError(f"{FILES['events']}: only {len(events)} events")
        national = states.pop("US", None)
        return {
            "vintage": f"NOAA NCEI billion-dollar disasters {FIRST_YEAR}–{LAST_YEAR} (final release)",
            "years": [FIRST_YEAR, LAST_YEAR],
            "final": True,
            "hazards": list(HAZARDS.values()),
            "unit": "USD, CPI-adjusted to the release's base year",
            "states": states,
            "national_by_state_file": national,
            "events": events,
            "_files": list(FILES.values()),
        }
