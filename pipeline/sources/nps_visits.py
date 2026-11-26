"""NPS Visitor Use Statistics (IRMA REST API): monthly recreation visits for every reporting park unit.

One request per calendar year returns all units (JSON when asked with Accept: application/json). The latest year in
which every unit has all 12 months is used; an incomplete year is not kept, so it is tried again next time.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from ..core.source import Source, SourceInfo

URL = "https://irmaservices.nps.gov/v3/rest/stats/visitation?startMonth=1&startYear={y}&endMonth=12&endYear={y}"
HEADERS = {"Accept": "application/json"}


def _complete(rows) -> bool:
    if not isinstance(rows, list):
        return False
    months = defaultdict(set)
    for r in rows:
        months[r["UnitCode"]].add(r["Month"])
    return bool(months) and all(len(m) == 12 for m in months.values())


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):  # e.g. XML or an error page instead of JSON
        return None


class NPSVisits(Source):
    info = SourceInfo(
        id="nps_visits",
        title="NPS Visitor Use Statistics: recreation visits by park unit",
        publisher="U.S. National Park Service, Visitor Use Statistics Office (IRMA)",
        homepage="https://irma.nps.gov/Stats/",
        license="Public domain (U.S. Government work)",
        description="Monthly recreation visits reported by each National Park System unit, summed over the latest "
        "complete calendar year. Units count visits with their own methods (road counters with per-vehicle "
        "multipliers, entrance gates, ticket sales), so parkways and urban recreation areas count through-traffic and "
        "the National Mall memorials each count the same visitors: visits are only compared within like unit types.",
        used_for=["Wonders: national parks ranked by recreation visits"],
    )

    def _years(self):
        return sorted((int(p.stem[7:]) for p in self.raw_dir.glob("visits_*.json")), reverse=True)

    def fetch(self, force=False):
        if self.offline:
            if not self._years():
                raise FileNotFoundError("nps_visits: nothing cached (--offline)")
            return
        for y in range(date.today().year - 1, date.today().year - 4, -1):
            rel = f"visits_{y}.json"
            path = self._get(y, rel, force)
            if path and _complete(_load(path)):
                print(f"    NPS visits {y} (all units have 12 months)")
                return
            if path:
                print(f"    NPS visits {y} incomplete, trying the year before")
                self.discard(rel)
        raise RuntimeError("no complete year of NPS visitor statistics found")

    def _get(self, y, rel, force):
        try:
            return self.download(URL.format(y=y), rel, force=force, headers=HEADERS)
        except Exception as e:  # noqa: BLE001 - network/HTTP problems: try the year before
            print(f"      {e}")
            return None

    def process(self):
        years = [y for y in self._years() if _complete(_load(self.raw_dir / f"visits_{y}.json"))]
        if not years:
            raise FileNotFoundError("nps_visits: no complete year cached; run fetch")
        y = years[0]
        rows = json.loads((self.raw_dir / f"visits_{y}.json").read_text())
        units: dict[str, dict] = {}
        for r in rows:
            u = units.setdefault(r["UnitCode"], {"name": r["UnitName"], "visits": 0, "months": 0})
            u["visits"] += int(r["RecreationVisitors"] or 0)
            u["months"] += 1
        total = sum(u["visits"] for u in units.values())
        print(f"    {len(units)} units, {total:,} recreation visits in {y}")
        return {"vintage": f"NPS recreation visits {y}", "_files": [f"visits_{y}.json"], "year": y,
                "total": total, "units": dict(sorted(units.items()))}
