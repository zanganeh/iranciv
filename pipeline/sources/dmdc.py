"""DMDC: military and DoD civilian personnel by duty state and service/component (quarterly workbook).

The file name carries the "as of" quarter as YYMM (e.g. ..._2609.xlsx = 30 Sep 2026). Quarters are probed
newest-first; some quarters are missing on the server (HTTP 500), so a few are tried.
"""

from __future__ import annotations

import re
from datetime import date

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked, num, xlsx_rows

URL = "https://dwp.dmdc.osd.mil/dwp/api/download?fileName=DMDC_Website_Location_Report_{yymm}.xlsx&groupName=milRegionCountry"
QUARTER_ENDS = (12, 9, 6, 3)

# Column letters of the "LOCATION COUNTRY REPORT" sheet (two-row header; checked against the header text below).
COLUMNS = {
    "active": {"army": "C", "navy": "D", "marines": "E", "air_force": "F", "space_force": "G", "coast_guard": "H",
               "total": "I"},
    "guard_reserve": {"army_guard": "J", "army_reserve": "K", "navy_reserve": "L", "marine_reserve": "M",
                      "air_guard": "N", "air_force_reserve": "O", "coast_guard_reserve": "P", "total": "Q"},
    "civilian": {"army": "R", "navy": "S", "marines": "T", "air_force": "U", "defense_wide": "V", "total": "W"},
}
HEADER = {"C": "ARMY", "D": "NAVY", "E": "MARINE CORPS", "F": "AIR FORCE", "G": "SPACE FORCE", "H": "COAST GUARD",
          "J": "ARMY NATIONAL GUARD", "K": "ARMY RESERVE", "L": "NAVY RESERVE", "M": "MARINE CORPS RESERVE",
          "N": "AIR NATIONAL GUARD", "O": "AIR FORCE RESERVE", "P": "COAST GUARD RESERVE", "V": "4TH ESTATE (DOD)"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _quarters(today: date, n: int = 8):
    y, m = today.year, today.month
    out = []
    while len(out) < n:
        for q in QUARTER_ENDS:
            if (y, q) < (today.year, today.month):
                out.append(f"{y % 100:02d}{q:02d}")
        y -= 1
    return sorted(set(out), reverse=True)[:n]


def _int(text):
    v = num(text)
    return None if v is None else int(round(v))


class DMDCPersonnel(Source):
    info = SourceInfo(
        id="dmdc_personnel",
        title="Military and DoD civilian personnel by duty state and service/component",
        publisher="U.S. Department of Defense, Defense Manpower Data Center (DMDC)",
        homepage="https://dwp.dmdc.osd.mil/dwp/app/dod-data-reports/workforce-reports",
        license="U.S. Government work, public",
        description="Quarterly counts of active-duty service members (Army, Navy, Marine Corps, Air Force, Space "
        "Force, Coast Guard), National Guard and Reserve members, and appropriated-fund DoD civilians, by the state of "
        "their assigned unit or duty station. State totals only: DMDC publishes no per-installation or per-county "
        "figures. Excludes classified data and most personnel on temporary duty or deployed; civilian counts leave out "
        "the Defense Logistics Agency (not submitted).",
        used_for=["Military advisor: troops by state and branch", "National armed-forces totals"],
    )

    def _cached(self):
        files = sorted((p.name for p in self.raw_dir.glob("location_*.xlsx")), reverse=True)
        return files[0] if files else None

    def fetch(self, force=False):
        if self.offline:
            if not self._cached():
                raise FileNotFoundError("dmdc_personnel: no cached workbook (--offline)")
            return
        for yymm in _quarters(date.today()):
            rel = f"location_{yymm}.xlsx"
            if download_checked(self, URL.format(yymm=yymm), rel, force=force, magic=b"PK", binary=True,
                                skip_errors=True):
                print(f"    DMDC location report {yymm}")
                return
        raise RuntimeError("no DMDC location report found in the last eight quarters")

    def process(self):
        rel = self._cached()
        if not rel:
            raise FileNotFoundError("dmdc_personnel: run fetch first")
        rows = [r for r in xlsx_rows(self.raw_dir / rel) if r]
        as_of = next((m.group(1) for r in rows[:8] if (m := re.search(r"As of (\w+ \d+, \d{4})", r.get("A", "")))),
                     None)
        sub = next((r for r in rows[:10] if r.get("C") == "ARMY" and r.get("J")), None)
        if not sub or any(sub.get(c, "").upper() != h for c, h in HEADER.items()):
            raise RuntimeError(f"DMDC header changed: {sub}")
        states, region, us_total = {}, None, None
        for r in rows:
            if r.get("A"):
                region = r["A"].strip().upper()
            name = (r.get("B") or "").strip()
            rec = {grp: {k: _int(r.get(col)) for k, col in cols.items()} for grp, cols in COLUMNS.items()}
            rec["total"] = _int(r.get("X"))
            if region == "UNITED STATES" and name and rec["total"] is not None:
                states[name.upper()] = rec
            elif region == "UNITED STATES TOTAL" and rec["total"] is not None:
                us_total = rec
        if len(states) < 50:
            raise RuntimeError(f"DMDC: only {len(states)} US rows parsed")
        print(f"    {len(states)} US duty states/territories, as of {as_of}; US total {us_total and us_total['total']:,}")
        return {"vintage": f"DMDC, as of {as_of}" if as_of else f"DMDC {rel}", "_files": [rel], "as_of": as_of,
                "states": states, "us_total": us_total}
