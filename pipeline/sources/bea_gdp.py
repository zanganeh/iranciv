"""BEA CAGDP2: GDP in current dollars by county and industry (keyless bulk zip).

BEA no longer publishes a keyless metro GDP file, so metro GDP is the sum of the
member counties (see build/cities.py). The file also has state and US rows.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile

from ..core.source import Source, SourceInfo

URL = "https://apps.bea.gov/regional/zip/CAGDP2.zip"
FILE = "CAGDP2.zip"
SUPPRESSED = {"(D)", "(NA)", "(NM)", "(L)", "(X)", "(S)"}  # (D) = withheld to avoid disclosure, (NA) = not available


class BeaGdp(Source):
    info = SourceInfo(
        id="bea_gdp",
        title="GDP by county and industry (CAGDP2)",
        publisher="U.S. Bureau of Economic Analysis",
        homepage="https://apps.bea.gov/regional/downloadzip.htm",
        license="Public domain (U.S. Government work); cite as U.S. Bureau of Economic Analysis",
        description="Gross domestic product in current dollars for every county, state and the nation, by "
        "industry. Metro GDP is the sum of the metro's counties. BEA withholds some county-industry values to "
        "protect confidential data; those are shown as 'no data', never as zero.",
        used_for=["Gold yield (GDP)", "GDP by industry in city and state panels"],
    )

    def fetch(self, force=False):
        # BEA answers missing files with HTTP 200 and an HTML page: expect_binary rejects a
        # text/html answer before it is saved; is_zipfile catches one with another Content-Type.
        path = self.download(URL, FILE, force=force, expect_binary=True)
        if not zipfile.is_zipfile(path):
            self.discard(FILE)
            raise RuntimeError(f"{URL} did not return a zip file (BEA may have moved it)")

    def process(self):
        with zipfile.ZipFile(self.raw_dir / FILE) as z:
            member = next(n for n in z.namelist() if re.fullmatch(r"CAGDP2__ALL_AREAS_\d{4}_\d{4}\.csv", n))
            text = z.read(member).decode("latin-1")
        reader = csv.reader(io.StringIO(text), skipinitialspace=True)
        header = [h.strip() for h in next(reader)]
        year_cols = [i for i, h in enumerate(header) if h.isdigit()]
        col = year_cols[-1]
        year = int(header[col])
        nation: dict[str, float | None] = {}
        states: dict[str, dict] = {}
        counties: dict[str, dict] = {}
        names: dict[str, str] = {}
        lines: dict[str, str] = {}
        for row in reader:
            if len(row) <= col:
                continue  # footnote lines at the end
            fips, name, line = row[0].strip(), row[1].strip(), row[4].strip()
            if not (fips.isdigit() and line.isdigit()):
                continue
            unit = row[7].strip()
            if unit != "Thousands of dollars":
                raise RuntimeError(f"unexpected unit {unit!r} in {member}")
            raw = row[col].strip()
            value = None if raw in SUPPRESSED or not raw else float(raw) * 1000
            lines.setdefault(line, row[6].strip().rstrip("0123456789/ ").strip())
            if fips == "00000":
                nation[line] = value
            elif fips.endswith("000") and fips < "90000":
                states.setdefault(fips[:2], {})[line] = value
            elif fips < "90000":
                counties.setdefault(fips, {})[line] = value
                names[fips] = name
        if not nation.get("1"):
            raise RuntimeError(f"{member}: no US total for {year}")
        return {
            "vintage": f"BEA CAGDP2, {year}",
            "year": year,
            "unit": "usd",
            "lines": lines,
            "nation": nation,
            "states": states,
            "counties": counties,
            "county_names": names,
            "_files": [FILE],
        }
