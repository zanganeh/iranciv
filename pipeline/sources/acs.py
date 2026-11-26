"""American Community Survey via the Census "table-based summary files".

These are bulk, pipe-delimited files – one per table, covering every geography –
so no API key is needed. The newest available year is discovered automatically.

The 5-year files hold every block group (up to 230 MB per table), but they are
sorted by GEO_ID and the county rows form one contiguous block near the top.
For county-only sources we therefore stream each file only up to the end of the
county block (about 0.1–2 MB per table) and record that in the manifest.
"""

from __future__ import annotations

import csv
from datetime import date

from ..core.fetch import url_exists
from ..core.source import Source, SourceInfo

BASE = "https://www2.census.gov/programs-surveys/acs/summary_file"

# Census "summary level" prefixes of GEO_ID values.
GEO_NATION = "0100000US"
GEO_STATE = "0400000US"
GEO_CBSAS = ("310M600US", "310M700US")  # metro + micro areas (2020 / 2023 delineations)
GEO_COUNTY = "0500000US"

MISSING = {-666666666, -999999999, -888888888, -222222222, -333333333, -555555555}


class AcsSource(Source):
    years: int  # 1 or 5
    tables: list[str]
    geo_prefixes: tuple[str, ...]
    prefix_only: str | None = None  # stream each file only through the block of GEO_IDs with this prefix

    def _url(self, year: int, table: str) -> str:
        return f"{BASE}/{year}/table-based-SF/data/{self.years}YRData/acsdt{self.years}y{year}-{table}.dat"

    def _rel(self, year: int, table: str) -> str:
        return f"{year}/{table}.dat"

    def latest_year(self) -> int:
        for year in range(date.today().year, date.today().year - 5, -1):
            if url_exists(self._url(year, self.tables[0])):
                return year
        raise RuntimeError(f"No ACS {self.years}-year release found")

    def fetch(self, force=False):
        if self.offline:  # no discovery request: use what is cached (download() raises if a file is missing)
            year = self._cached_year()
            print(f"    ACS {self.years}-year {year} (--offline: newest complete cached year)")
        else:
            year = self.latest_year()
            print(f"    ACS {self.years}-year {year}")
        for t in self.tables:
            if self.prefix_only:
                self.download_prefix(self._url(year, t), self._rel(year, t), self.prefix_only, header_start="GEO_ID|", force=force)
            else:
                self.download(self._url(year, t), self._rel(year, t), force=force)

    def _cached_year(self) -> int:
        """Newest downloaded year for which every table is present."""
        years = sorted((int(p.name) for p in self.raw_dir.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
        for y in years:
            if all((self.raw_dir / self._rel(y, t)).exists() for t in self.tables):
                return y
        raise FileNotFoundError(f"{self.id}: no year with all of {self.tables} downloaded yet")

    def _read(self, year: int, tables: list[str]) -> tuple[dict, dict]:
        """(geos, cbsa) estimate dicts {geo or 5-digit CBSA code: {"B01003_001": value|None, ...}}."""
        geos: dict[str, dict[str, float | None]] = {}
        cbsa: dict[str, dict[str, float | None]] = {}
        for t in tables:
            with open(self.raw_dir / self._rel(year, t), newline="", encoding="latin-1") as f:
                reader = csv.reader(f, delimiter="|")
                header = next(reader)
                est_cols = [(i, h.replace("_E", "_")) for i, h in enumerate(header) if "_E" in h]
                for row in reader:
                    geo = row[0]
                    if geo.startswith(GEO_CBSAS) and geo.startswith(self.geo_prefixes):
                        rec = cbsa.setdefault(geo[-5:], {})  # key metros by their 5-digit CBSA code
                    elif geo.startswith(self.geo_prefixes):
                        rec = geos.setdefault(geo, {})
                    else:
                        continue
                    for i, col in est_cols:
                        try:
                            num = float(row[i])
                        except (ValueError, IndexError):
                            num = None
                        rec[col] = None if num is None or num in MISSING else num
        return geos, cbsa

    def process(self):
        year = self._cached_year()
        geos, cbsa = self._read(year, self.tables)
        out = {
            "vintage": f"ACS {year} {self.years}-year",
            "year": year,
            "geos": geos,
            "cbsa": cbsa,
            "_files": [self._rel(year, t) for t in self.tables],
        }
        return out


class AcsMetro1Yr(AcsSource):
    info = SourceInfo(
        id="acs_1yr",
        title="American Community Survey, 1-year estimates",
        publisher="U.S. Census Bureau",
        homepage="https://www.census.gov/programs-surveys/acs",
        license="Public domain (U.S. Government work)",
        description="Population, jobs by industry, household income, rent, home values and unemployment for the "
        "nation, the states and the metro/micro areas published in the ACS 1-year release (roughly 65,000+ "
        "people), plus the citizen mix of each metro: workers by occupation (C24010), people by age (B01001, "
        "B01002), how workers get to work (C08301), education of adults 25+ (C15003) and place of birth "
        "(B05002). Census leaves a few small areas out of some of these tables entirely. (Population growth "
        "comes from the Census population estimates instead, which compare every year on the same borders.)",
        used_for=["City yields & panels", "State panels", "National top bar", "City View citizens"],
    )
    years = 1
    tables = [
        "b01003", "c24030", "b19025", "b19013", "b25064", "b25077", "b23025",
        # citizens (City View): raw male/female lines are kept; builders add the sexes together
        "c24010",  # sex by occupation, civilian employed 16+ (male 002-037, female = male + 36)
        "b01001",  # sex by age (male 003-025, female = male + 24)
        "b01002",  # median age
        "c08301",  # means of transportation to work (collapsed; 011 = worked from home)
        "c15003",  # educational attainment 25+ (collapsed)
        "b05002",  # place of birth by nativity and citizenship (013 = foreign born)
    ]
    geo_prefixes = (GEO_NATION, GEO_STATE, *GEO_CBSAS)


class AcsCounty5Yr(AcsSource):
    info = SourceInfo(
        id="acs_5yr",
        title="American Community Survey, 5-year estimates (counties)",
        publisher="U.S. Census Bureau",
        homepage="https://www.census.gov/programs-surveys/acs",
        license="Public domain (U.S. Government work)",
        description="County population, income, home value, education, age, poverty, broadband, commuting, "
        "birthplace, homeownership, health insurance, veterans (civilians 18+) and members of the armed forces "
        "(by where they live), pooled over five years so every county (not just large ones) has an estimate. "
        "Only the county rows of each national table file are downloaded.",
        used_for=[
            "County lenses (people, prosperity, knowledge, daily life)",
            "Military layer (veterans and armed forces by county)",
            "Metro GDP (population share of a BEA combined county area split between metros)",
        ],
    )
    years = 5
    tables = [
        "b01003",  # total population
        "b19013",  # median household income
        "b25077",  # median home value
        "b15003",  # educational attainment, 25+
        "b01002",  # median age
        "b17001",  # poverty status
        "b28002",  # internet subscriptions (households)
        "b08013",  # aggregate travel time to work
        "b08303",  # travel time to work (workers not working from home)
        "b05012",  # nativity
        "b25003",  # tenure
        "b27010",  # health insurance coverage by age
        "b21001",  # veteran status, civilian population 18+ (002 = veterans)
        "b23025",  # employment status 16+ (006 = armed forces)
    ]
    geo_prefixes = (GEO_COUNTY,)
    prefix_only = GEO_COUNTY
