"""BLS Quarterly Census of Employment and Wages (QCEW), annual averages by county, for a set of industries.

QCEW counts jobs covered by unemployment insurance (about 95% of U.S. jobs) by the county of the employer's
establishment. BLS publishes one small CSV "slice" per industry and year covering every county, the states and
the nation (https://www.bls.gov/cew/additional-resources/open-data/csv-data-slices.htm), so only the ~40 industries
the districts use are downloaded (about 15 MB) instead of the 440 MB single file.

From the 2025 annual data on, BLS publishes metropolitan areas as totals only; metro values by industry are
therefore built by adding up member counties (pipeline/build/districts.py), as BLS itself advises.

Suppression: a county row whose disclosure_code is "N" is published with zeros. It is kept as `null`
(unknown) here, never as 0, so sums over counties can say they are lower bounds.

Interim shape (`data`):
    year, vintage
    codes:  {naics: title}                        industries in the order fetched
    owns:   [1, 2, 3, 5]                           the ownership codes of every per-county list below
    us:     {naics: [[emp, wages, oty_chg] | null for each own]}  (US000 rows; null = suppressed or no row)
    us_total: [emp, wages, oty_chg]                all industries, all ownerships (US000, own 0)
    totals: {fips: [emp, wages, oty_chg] | null}   county all-industry totals (own 0); null = suppressed
    counties: {naics: {fips: [entry for each own]}}  entry: [emp, wages, oty_chg]; null = suppressed;
                                                   0 = that ownership has no row (no such jobs)
Only real counties are kept (statewide "SS999" unallocated and "SS000" state rows are dropped).
"""

from __future__ import annotations

import csv
from datetime import date

from ..core.fetch import url_exists
from ..core.source import Source, SourceInfo

BASE = "https://data.bls.gov/cew/data/api"

# NAICS 2022 codes as used by the API slices (ranges use underscores). Titles are BLS's own.
INDUSTRIES: dict[str, str] = {
    "10": "Total, all industries",
    # industrial zone
    "31_33": "Manufacturing",
    "3361": "Motor vehicle manufacturing",
    "3344": "Semiconductor and other electronic component manufacturing",
    "3254": "Pharmaceutical and medicine manufacturing",
    "3241": "Petroleum and coal products manufacturing",
    "331": "Primary metal manufacturing",
    "211": "Oil and gas extraction",
    "2121": "Coal mining",
    # commercial hub
    "52": "Finance and insurance",
    "5231": "Securities and commodity contracts intermediation and brokerage",
    "524": "Insurance carriers and related activities",
    # campus
    "5417": "Scientific research and development services",
    "6113": "Colleges, universities, and professional schools",
    "5415": "Computer systems design and related services",
    "5132": "Software publishers",
    "5182": "Computing infrastructure providers, data processing, web hosting, and related services",
    # harbor
    "483": "Water transportation",
    "4883": "Support activities for water transportation",
    # theater square
    "711": "Performing arts, spectator sports, and related industries",
    "512": "Motion picture and sound recording industries",
    "516": "Broadcasting and content providers",
    # entertainment complex
    "713": "Amusement, gambling, and recreation industries",
    "7132": "Gambling industries",
    "721": "Accommodation",
    # aerodrome
    "481": "Air transportation",
    "4881": "Support activities for air transportation",
    "3364": "Aerospace product and parts manufacturing",
    # government plaza (public administration without national security)
    "921": "Executive, legislative, and other general government support",
    "922": "Justice, public order, and safety activities",
    "923": "Administration of human resource programs",
    "924": "Administration of environmental quality programs",
    "925": "Administration of housing programs, urban planning, and community development",
    "926": "Administration of economic programs",
    "927": "Space research and technology",
    # encampment
    "928110": "National security",
    # aqueduct / dam
    "22": "Utilities",
    "2211": "Electric power generation, transmission and distribution",
    "2213": "Water, sewage and other systems",
}
OWNS = [1, 2, 3, 5]  # federal, state, local government, private


def _num(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _is_county(fips: str) -> bool:
    return len(fips) == 5 and fips.isdigit() and not fips.endswith(("000", "999"))


class BlsQcew(Source):
    info = SourceInfo(
        id="bls_qcew",
        title="Quarterly Census of Employment and Wages, annual averages by county",
        publisher="U.S. Bureau of Labor Statistics",
        homepage="https://www.bls.gov/cew/",
        license="Public domain (U.S. Government work)",
        description="Jobs and wages by county for about 40 industries (manufacturing, autos, chips, finance, "
        "research labs, universities, software, ports, film, casinos, hotels, airlines, aerospace, public "
        "administration, civilian defense staff, utilities and more), from the annual averages of the "
        "Quarterly Census of Employment and Wages: a count of the jobs covered by unemployment insurance, by "
        "the county where the employer's establishment is. It leaves out the self-employed, most farm "
        "workers, railroads and uniformed military. BLS publishes metro areas as totals only from 2025 on, so "
        "metro figures add up their member counties. County rows withheld for confidentiality are left out, "
        "so such sums are lower bounds.",
        used_for=["City View districts (industry specialisation)", "City type badges"],
    )

    def _url(self, year: int, code: str) -> str:
        return f"{BASE}/{year}/a/industry/{code}.csv"

    def _rel(self, year: int, code: str) -> str:
        return f"{year}/{code}.csv"

    def latest_year(self) -> int:
        this = date.today().year
        for year in range(this, this - 4, -1):
            if url_exists(self._url(year, "10")):
                return year
        raise RuntimeError("No QCEW annual release found")

    def _cached_year(self) -> int:
        if not self.raw_dir.exists():
            raise FileNotFoundError(f"{self.id}: nothing downloaded yet")
        years = sorted((int(p.name) for p in self.raw_dir.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
        for y in years:
            if all((self.raw_dir / self._rel(y, c)).exists() for c in INDUSTRIES):
                return y
        raise FileNotFoundError(f"{self.id}: no year with all {len(INDUSTRIES)} industry slices downloaded yet")

    def fetch(self, force=False):
        if self.offline:
            year = self._cached_year()
            print(f"    QCEW {year} annual (--offline: newest complete cached year)")
        else:
            year = self.latest_year()
            print(f"    QCEW {year} annual averages, {len(INDUSTRIES)} industry slices")
        for code in INDUSTRIES:
            self.download(self._url(year, code), self._rel(year, code), force=force)

    def process(self):
        year = self._cached_year()
        us: dict[str, list] = {}
        counties: dict[str, dict[str, list]] = {}
        totals: dict[str, list | None] = {}
        us_total = None
        for code in INDUSTRIES:
            us_row: list = [None] * len(OWNS)
            per: dict[str, list] = {}
            with open(self.raw_dir / self._rel(year, code), newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    fips, own = row["area_fips"], int(row["own_code"])
                    if row["qtr"] != "A":
                        continue
                    suppressed = row["disclosure_code"].strip() == "N"
                    entry = None if suppressed else [
                        int(_num(row["annual_avg_emplvl"])),
                        int(_num(row["total_annual_wages"])),
                        int(_num(row["oty_annual_avg_emplvl_chg"])),
                    ]
                    if code == "10":
                        if own != 0:
                            continue
                        if fips == "US000":
                            us_total = entry
                        elif _is_county(fips):
                            totals[fips] = entry
                        continue
                    if own not in OWNS:
                        continue
                    k = OWNS.index(own)
                    if fips == "US000":
                        us_row[k] = entry
                    elif _is_county(fips):
                        per.setdefault(fips, [0] * len(OWNS))[k] = entry
            if code != "10":
                us[code] = us_row
                counties[code] = per
        if not us_total:
            raise ValueError("QCEW: the US all-industry total is missing")
        return {
            "vintage": f"BLS QCEW {year} annual averages",
            "year": year,
            "codes": dict(INDUSTRIES),
            "owns": OWNS,
            "us": us,
            "us_total": us_total,
            "totals": totals,
            "counties": counties,
            "_files": [self._rel(year, c) for c in INDUSTRIES],
        }
