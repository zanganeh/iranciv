"""NSF NCSES Higher Education Research and Development (HERD) Survey: public microdata, total R&D by institution.

The microdata page lists one zip per fiscal year; the newest is used. The zip holds a long-format CSV (one row per
institution x question x row x column, `data` in thousands of dollars) and a large SAS file, which is not read.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked

SITE = "https://ncses.nsf.gov"
PAGE = SITE + "/explore-data/microdata/higher-education-research-development"
FALLBACK = SITE + "/821/assets/0/files/higher_education_r_and_d_2025.zip"
ZIP_LINK = re.compile(r'href="([^"]*higher_education_r_and_d_(\d{4})\.zip)"')

# Combined or system reports without an IPEDS UNITID in the microdata → the campus that leads the report
# (checked against IPEDS HD names). Only clear one-campus leads are mapped; system-wide reports are left out.
CROSSWALK = {
    "Johns Hopkins University": "162928",
    "Ohio State University, The": "204796",
    "University of Maryland": "163286",  # College Park
    "Texas A&M University, College Station and Health Science Center": "228723",
    "Vanderbilt University and Vanderbilt University Medical Center": "221999",
    "University of Cincinnati": "201885",
    "University of Nebraska, Lincoln and Medical Center": "181464",
    "University of Connecticut": "129020",
    "Oregon State University": "209542",
}


class HERD(Source):
    info = SourceInfo(
        id="herd",
        title="Higher Education Research and Development (HERD) Survey, public microdata",
        publisher="National Science Foundation, National Center for Science and Engineering Statistics (NCSES)",
        homepage=PAGE,
        license="Public domain (U.S. Government work)",
        description="Research and development spending reported by each US college and university for one fiscal "
        "year: total R&D from all sources and the federally funded part. Totals include medical schools, and some "
        "universities report together with their medical centre (e.g. Vanderbilt with VUMC); a few combined reports "
        "without an IPEDS ID are matched to their lead campus by hand.",
        used_for=["Wonders: great research universities ranked by R&D spending"],
    )

    def _cached(self):
        years = sorted((int(m.group(1)) for p in self.raw_dir.glob("herd_*.zip")
                        if (m := re.match(r"herd_(\d{4})\.zip", p.name))), reverse=True)
        return years[0] if years else None

    def fetch(self, force=False):
        if self.offline:
            if self._cached() is None:
                raise FileNotFoundError("herd: nothing cached (--offline)")
            return
        page = download_checked(self, PAGE, "microdata.html", force=True, skip_errors=True)
        links = {}
        if page:
            for href, y in ZIP_LINK.findall(page.read_text(errors="replace")):
                links[int(y)] = href if href.startswith("http") else SITE + href
        if not links:
            links = {2025: FALLBACK}
        for y in sorted(links, reverse=True)[:3]:
            if download_checked(self, links[y], f"herd_{y}.zip", force=force, magic=b"PK", binary=True,
                                skip_errors=True):
                print(f"    HERD FY{y} microdata")
                return
        raise RuntimeError("no HERD microdata zip could be downloaded")

    def process(self):
        y = self._cached()
        if y is None:
            raise FileNotFoundError("herd: run fetch first")
        rel = f"herd_{y}.zip"
        inst: dict[str, dict] = {}
        with zipfile.ZipFile(self.raw_dir / rel) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(name) as f:
                for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")):
                    q = r["questionnaire_no"]
                    if q not in ("01.a", "01.g"):
                        continue
                    key = r["inst_id"]
                    rec = inst.setdefault(key, {
                        "name": " ".join(r["inst_name_long"].split()), "city": r["inst_city"],
                        "state": r["inst_state_code"], "unitid": (r["ipeds_unitid"] or "").strip() or None,
                        "med_school": r.get("med_sch_flag") == "T", "rd_usd": None, "federal_usd": None,
                        "unitid_matched_by_hand": False})
                    try:
                        usd = float(r["data"]) * 1000  # file is in thousands of dollars
                    except ValueError:
                        usd = None
                    rec["rd_usd" if q == "01.g" else "federal_usd"] = usd
        for rec in inst.values():
            if not rec["unitid"] and rec["name"] in CROSSWALK:
                rec["unitid"], rec["unitid_matched_by_hand"] = CROSSWALK[rec["name"]], True
        rows = sorted(inst.values(), key=lambda r: -(r["rd_usd"] or 0))
        print(f"    HERD FY{y}: {len(rows)} institutions; #1 {rows[0]['name']} ${rows[0]['rd_usd'] / 1e9:,.2f}B; "
              f"{sum(1 for r in rows if not r['unitid'])} without an IPEDS id")
        return {"vintage": f"NSF HERD FY{y}", "_files": [rel], "year": y, "institutions": rows}
