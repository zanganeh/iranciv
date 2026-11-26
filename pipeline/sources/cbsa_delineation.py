"""OMB metro/micro delineations (July 2023): which counties form each CBSA, and its principal cities.

Published by the Census Bureau only as .xlsx, which is a zip of XML, so it is read
with zipfile + ElementTree.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

from ..core.source import Source, SourceInfo

BASE = "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files"
FILES = {"counties": "list1_2023.xlsx", "principal_cities": "list2_2023.xlsx"}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def xlsx_rows(path) -> list[list[str]]:
    """All rows of the first worksheet as lists of strings (empty cells → '')."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for r in sheet.iter(f"{NS}row"):
        cells: dict[int, str] = {}
        for c in r.iter(f"{NS}c"):
            letters = re.match(r"[A-Z]+", c.get("r", "A")).group()
            col = 0
            for ch in letters:
                col = col * 26 + ord(ch) - 64
            v = c.find(f"{NS}v")
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            else:
                val = v.text if v is not None and v.text else ""
            cells[col - 1] = val.strip()
        rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    return rows


def table(path) -> list[dict[str, str]]:
    """Rows below the header row (the first row that starts with 'CBSA Code') as dicts."""
    rows = xlsx_rows(path)
    start = next(i for i, r in enumerate(rows) if r and r[0] == "CBSA Code")
    header = rows[start]
    return [dict(zip(header, r)) for r in rows[start + 1 :] if r and re.fullmatch(r"\d{5}", r[0])]


class CbsaDelineation(Source):
    info = SourceInfo(
        id="cbsa_delineation",
        title="Core Based Statistical Area delineation files (July 2023)",
        publisher="U.S. Census Bureau / Office of Management and Budget (Bulletin 23-01)",
        homepage="https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html",
        license="Public domain (U.S. Government work)",
        description="The official list of counties that make up each metro and micro area, and of each area's "
        "principal cities. Used to place each city at its first principal city and to add up county GDP into "
        "metro GDP, and to confirm that the population estimates behind growth use the same county lists.",
        used_for=["City locations", "Metro GDP (sum of member counties)", "Population growth (county-list check)"],
    )

    def fetch(self, force=False):
        for name in FILES.values():
            path = self.download(f"{BASE}/{name}", name, force=force, expect_binary=True)
            if not zipfile.is_zipfile(path):
                self.discard(name)
                raise RuntimeError(f"{path}: not an xlsx file (the server may have sent an error page)")

    def process(self):
        counties: dict[str, list[str]] = {}
        titles: dict[str, str] = {}
        for r in table(self.raw_dir / FILES["counties"]):
            fips = r["FIPS State Code"].zfill(2) + r["FIPS County Code"].zfill(3)
            counties.setdefault(r["CBSA Code"], []).append(fips)
            titles[r["CBSA Code"]] = r["CBSA Title"]
        principal: dict[str, list[dict]] = {}
        for r in table(self.raw_dir / FILES["principal_cities"]):
            principal.setdefault(r["CBSA Code"], []).append(
                {
                    "name": r["Principal City Name"],
                    "place_geoid": r["FIPS State Code"].zfill(2) + r["FIPS Place Code"].zfill(5),
                }
            )
        return {
            "vintage": "July 2023 delineations",
            "titles": titles,
            "counties": {k: sorted(v) for k, v in counties.items()},
            "principal_cities": principal,  # in the file's order: the first is the main one
            "_files": list(FILES.values()),
        }
