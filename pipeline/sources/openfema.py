"""OpenFEMA Disaster Declarations Summaries (v2): FEMA declarations by county, keyless REST API.

Only major-disaster declarations (DR) since 2000, without Biological (the 2020 COVID-19 declarations
named every county), are kept. The API is live and refreshed daily, so each fetch is saved as a dated
set of pages (`dr_since_2000/<YYYY-MM-DD>/page_NN.csv`); a fetch reuses the newest complete set while it
is younger than REFRESH_DAYS, and process() reads the newest complete set.

FEMA still files Connecticut under its 8 old counties, while the map uses the 2022 planning regions, so
the Census Bureau's town-level crosswalk between the two is fetched too.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.parse
import zipfile
from datetime import date, datetime, timezone

from ._resource_util import xlsx_records
from ..core.source import Source, SourceInfo

API = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries.csv"
SINCE = "2000-01-01"
TOP = 10000  # OpenFEMA's maximum page size
SELECT = ("disasterNumber,state,declarationType,declarationDate,incidentType,declarationTitle,incidentBeginDate,"
          "incidentEndDate,fipsStateCode,fipsCountyCode,designatedArea")
FILTER = f"declarationDate ge '{SINCE}' and declarationType eq 'DR' and incidentType ne 'Biological'"
SET_DIR = "dr_since_2000"
REFRESH_DAYS = 7

CT_URL = "https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.xlsx"
CT_FILE = "ct_cou_to_cousub_crosswalk.xlsx"

# Retired county codes that FEMA rows may still use → the code on the map.
RENAMED = {"46113": "46102", "51515": "51019"}


def page_url(skip: int) -> str:
    q = {"$filter": FILTER, "$select": SELECT, "$orderby": "id", "$top": str(TOP), "$skip": str(skip)}
    return API + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote, safe="$,'")


def _rows(path) -> int:
    with open(path, encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)


class OpenFemaDeclarations(Source):
    info = SourceInfo(
        id="fema_declarations",
        title="OpenFEMA Disaster Declarations Summaries (v2)",
        publisher="Federal Emergency Management Agency (FEMA)",
        homepage="https://www.fema.gov/openfema-data-page/disaster-declarations-summaries-v2",
        license="Public data (OpenFEMA terms): cite FEMA as the source; no FEMA endorsement implied. "
        "Connecticut crosswalk: U.S. Census Bureau, public domain.",
        description="Every county named in a presidential major-disaster declaration (DR) since 1 January 2000, "
        "with the incident type. Each disaster is counted once per county (FEMA lists New England towns "
        "separately under the same county code). Biological declarations (the 2020 COVID-19 pandemic, which "
        "named every county) and emergency (EM) and fire-management (FM) declarations are left out. A "
        "declaration is an administrative act that unlocks federal aid, not a measure of damage; statewide "
        "declarations count for the state only. Connecticut's 8 old counties are mapped to the planning "
        "regions that contain any of their towns (Census Bureau crosswalk), so a region counts a disaster if "
        "it overlaps a declared county.",
        used_for=["Disasters lens", "disasters.json (Disasters advisor and City View hazards)",
                  "County Explorer disasters column"],
    )

    def _sets(self, complete_only=True) -> list[str]:
        root = self.raw_dir / SET_DIR
        if not root.is_dir():
            return []
        out = []
        for d in sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)):
            pages = sorted(d.glob("page_*.csv"))
            if not complete_only or (pages and _rows(pages[-1]) < TOP):
                out.append(d.name)
        return out

    def fetch(self, force=False):
        self.download(CT_URL, CT_FILE, force=force, expect_binary=True)
        if not zipfile.is_zipfile(self.raw_dir / CT_FILE):
            self.discard(CT_FILE)
            raise RuntimeError(f"{CT_URL} did not return an xlsx file")
        sets = self._sets()
        if sets and (self.offline or not force):
            age = (datetime.now(timezone.utc).date() - date.fromisoformat(sets[-1])).days
            if self.offline or age < REFRESH_DAYS:
                print(f"    using declarations fetched {sets[-1]} ({age} days old; refresh after {REFRESH_DAYS})")
                return
        today = datetime.now(timezone.utc).date().isoformat()
        n = 0
        while True:
            rel = f"{SET_DIR}/{today}/page_{n:02d}.csv"
            path = self.download(page_url(n * TOP), rel, force=force)
            with open(path, encoding="utf-8") as f:
                if not f.readline().startswith("disasterNumber"):
                    self.discard(rel)
                    raise RuntimeError(f"OpenFEMA page {n} is not the expected CSV")
            if _rows(path) < TOP:
                break
            n += 1
            if n > 50:
                raise RuntimeError("OpenFEMA: more than 50 pages; the filter may have stopped working")
        for old in self._sets(complete_only=False):  # keep only the newest complete set
            if old != today:
                for p in sorted((self.raw_dir / SET_DIR / old).glob("*")):
                    self.discard(f"{SET_DIR}/{old}/{p.name}")
                try:
                    (self.raw_dir / SET_DIR / old).rmdir()
                except OSError:
                    pass

    def _ct_crosswalk(self) -> dict[str, list[str]]:
        """Old CT county GEOID → planning-region GEOIDs containing any of its towns."""
        out: dict[str, set] = {}
        for rec in xlsx_records(self.raw_dir / CT_FILE, "COUSUBFP"):
            k = {re.sub(r"\s.*", "", key, flags=re.S): v for key, v in rec.items()}
            st, old, new = k.get("STATEFP"), k.get("OLD_COUNTYFP"), k.get("NEW_COUNTYFP")
            if st == "09" and old and new and old.isdigit() and new.isdigit():
                out.setdefault(st + old, set()).add(st + new)
        if len(out) != 8:
            raise RuntimeError(f"{CT_FILE}: expected 8 old Connecticut counties, found {len(out)}")
        return {k: sorted(v) for k, v in sorted(out.items())}

    def process(self):
        sets = self._sets()
        if not sets:
            raise RuntimeError("no complete OpenFEMA declaration set cached – run: uv run python -m pipeline fetch fema_declarations")
        as_of = sets[-1]
        pages = sorted((self.raw_dir / SET_DIR / as_of).glob("page_*.csv"))
        disasters: dict[int, dict] = {}
        county_pairs = set()
        rows = 0
        for p in pages:
            for row in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8"))):
                rows += 1
                num = int(row["disasterNumber"])
                d = disasters.setdefault(num, {
                    "state": row["state"], "type": row["incidentType"], "title": (row["declarationTitle"] or "").strip(),
                    "date": row["declarationDate"][:10], "begin": (row["incidentBeginDate"] or "")[:10] or None,
                    "end": (row["incidentEndDate"] or "")[:10] or None, "counties": set(), "statewide": False,
                })
                st, co = (row["fipsStateCode"] or "").zfill(2), (row["fipsCountyCode"] or "").zfill(3)
                if co == "000":
                    d["statewide"] = True  # statewide or tribal designation: counts for the state only
                else:
                    g = RENAMED.get(st + co, st + co)
                    d["counties"].add(g)
                    county_pairs.add((num, g))
        if len(disasters) < 1000:
            raise RuntimeError(f"OpenFEMA set {as_of}: only {len(disasters)} disasters (expected ~2,000+)")
        out = {
            str(n): {**d, "counties": sorted(d["counties"])}
            for n, d in sorted(disasters.items())
        }
        return {
            "vintage": f"OpenFEMA declarations as of {as_of}",
            "as_of": as_of,
            "window_start": SINCE,
            "rule": "Major-disaster (DR) declarations since " + SINCE + ", Biological excluded; one entry per "
            "disaster number with the set of counties it names.",
            "rows": rows,
            "county_pairs": len(county_pairs),
            "disasters": out,
            "ct_crosswalk": self._ct_crosswalk(),
            "_files": [f"{SET_DIR}/{as_of}/{p.name}" for p in pages] + [CT_FILE],
        }
