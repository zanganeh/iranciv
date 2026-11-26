"""IPEDS institutional directory (HD) and 12-month enrollment (DRVEF12) from the NCES IPEDS Data Center.

The newest collection year is discovered by probing HD<year>.zip; the enrollment file of the same year is used
when published (else the newest earlier one).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

from ..core.fetch import url_exists
from ..core.source import Source, SourceInfo

BASE = "https://nces.ed.gov/ipeds/datacenter/data/"


def _read_csv(path, stem: str):
    """Rows of the single CSV in an IPEDS zip (UTF-8 with a BOM in recent years, cp1252 in older ones)."""
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".csv") and "_rv" in n.lower()), None) or \
            next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(name)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return csv.DictReader(io.StringIO(text.lstrip("﻿")))


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class IPEDS(Source):
    info = SourceInfo(
        id="ipeds",
        title="IPEDS institutional characteristics (HD) and 12-month enrollment (DRVEF12)",
        publisher="U.S. Department of Education, National Center for Education Statistics (NCES)",
        homepage="https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx",
        license="Public domain (U.S. Government work)",
        description="The directory of every US college and university: name, location (coordinates, county and "
        "metro), control, and the 2025 Carnegie research designation (R1 = very high research spending and "
        "doctorates), plus 12-month unduplicated headcount enrollment, which also counts online-only students.",
        used_for=["Wonders: research-university positions and R1 status", "University enrollment"],
    )

    def _cached(self, stem):
        years = sorted((int(p.stem[len(stem):]) for p in self.raw_dir.glob(f"{stem}[0-9][0-9][0-9][0-9].zip")),
                       reverse=True)
        return years[0] if years else None

    def fetch(self, force=False):
        for stem in ("HD", "DRVEF12"):
            if self.offline:
                if self._cached(stem) is None:
                    raise FileNotFoundError(f"ipeds: {stem} not cached (--offline)")
                continue
            for y in range(date.today().year, date.today().year - 5, -1):
                url = f"{BASE}{stem}{y}.zip"
                if url_exists(url):
                    self.download(url, f"{stem}{y}.zip", force=force, expect_binary=True)
                    print(f"    IPEDS {stem}{y}")
                    break
            else:
                raise RuntimeError(f"no IPEDS {stem} file found")

    def process(self):
        hd_y, ef_y = self._cached("HD"), self._cached("DRVEF12")
        if hd_y is None:
            raise FileNotFoundError("ipeds: run fetch first")
        enroll = {}
        if ef_y is not None:
            for r in _read_csv(self.raw_dir / f"DRVEF12{ef_y}.zip", "DRVEF12"):
                enroll[r["UNITID"].strip()] = (_int(r.get("UNDUP")), _int(r.get("FTE12MN")))
        inst = {}
        for r in _read_csv(self.raw_dir / f"HD{hd_y}.zip", "HD"):
            if r.get("CYACTIVE", "1").strip() != "1":
                continue
            uid = r["UNITID"].strip()
            county = (r.get("COUNTYCD") or "").strip()
            cbsa = (r.get("CBSA") or "").strip()
            undup, fte = enroll.get(uid, (None, None))
            inst[uid] = {
                "name": " ".join(r["INSTNM"].split()),
                "city": r.get("CITY", "").strip(),
                "state": r.get("STABBR", "").strip(),
                "county": county.zfill(5) if county.isdigit() and int(county) > 0 else None,
                "cbsa": cbsa.zfill(5) if cbsa.isdigit() and int(cbsa) > 0 else None,
                "lat": _float(r.get("LATITUDE")), "lon": _float(r.get("LONGITUD")),
                "research": _int(r.get("CARNEGIERSCH")),
                "control": _int(r.get("CONTROL")),
                "enrollment": undup if undup and undup > 0 else None,
                "fte": fte if fte and fte > 0 else None,
            }
        r1 = sum(1 for i in inst.values() if i["research"] == 1)
        print(f"    {len(inst)} active institutions (HD{hd_y}), {r1} R1; enrollment from DRVEF12{ef_y}")
        files = [f"HD{hd_y}.zip"] + ([f"DRVEF12{ef_y}.zip"] if ef_y is not None else [])
        return {"vintage": f"IPEDS {hd_y}-{(hd_y + 1) % 100:02d} directory; 12-month enrollment {ef_y}",
                "_files": files, "year": hd_y, "enrollment_year": ef_y, "institutions": inst}
