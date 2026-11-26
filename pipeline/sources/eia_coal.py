"""EIA/MSHA mine-level coal production (annual)."""

from __future__ import annotations

import re
from datetime import date

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked, num, xlsx_records

BASE = "https://www.eia.gov/coal/data/public/xls/"
FILE = re.compile(r"coalpublic(\d{4})\.xlsx$")


class EIACoal(Source):
    info = SourceInfo(
        id="eia_coal",
        title="Historical Detailed Coal Production Data (mine level)",
        publisher="U.S. Energy Information Administration, with MSHA",
        homepage="https://www.eia.gov/coal/data.php",
        license="Public domain (U.S. Government work)",
        description="Annual production, employees and operator of every US coal mine, keyed by MSHA mine ID.",
        used_for=["Coal-mine resources"],
    )

    def _cached(self):
        files = [(int(m.group(1)), p) for p in self.raw_dir.glob("coalpublic*.xlsx") if (m := FILE.search(p.name))]
        return max(files) if files else None

    def fetch(self, force=False):
        if self._cached() and not force:
            return
        # The newest year appears some months after it ends; try newest first.
        for year in range(date.today().year, date.today().year - 4, -1):
            name = f"coalpublic{year}.xlsx"
            if download_checked(self, BASE + name, name, force=force, magic=b"PK", binary=True,
                                skip_errors=True):
                return
        raise RuntimeError("no coalpublic<year>.xlsx could be downloaded")

    def process(self):
        cached = self._cached()
        if not cached:
            raise FileNotFoundError("no coal workbook – run: uv run python -m pipeline fetch eia_coal")
        year, path = cached
        mines = []
        for r in xlsx_records(path, "MSHA ID"):
            tons = num(r.get("Production (short tons)"))
            if not r.get("MSHA ID") or not tons or tons <= 0:
                continue  # preparation plants and idle mines report 0
            mines.append({
                "msha_id": r["MSHA ID"].split(".")[0].zfill(7),
                "name": r["Mine Name"],
                "state": r["Mine State"],
                "county": r["Mine County"],
                "type": r["Mine Type"],
                "operator": r["Operating Company"],
                "region": r["Coal Supply Region"],
                "tons": int(tons),
                "employees": int(e) if (e := num(r.get("Average Employees"))) is not None else None,
            })
        mines.sort(key=lambda m: -m["tons"])
        print(f"    {len(mines)} producing mines in {year}, {sum(m['tons'] for m in mines) / 1e6:.1f} M short tons")
        return {"vintage": f"EIA coal production {year}", "_files": [path.name], "year": year, "mines": mines}
