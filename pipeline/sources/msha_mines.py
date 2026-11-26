"""MSHA Mines data set: every US mine with status, commodity, employees and coordinates."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

from ..core.source import Source, SourceInfo

URL = "https://arlweb.msha.gov/OpenGovernmentData/DataSets/Mines.zip"

# MSHA PRIMARY_SIC text → commodity id used on the map. Only metals and strategic minerals.
COMMODITY = {
    "Gold Ore": "gold",
    "Silver Ore": "silver",
    "Copper Ore NEC": "copper",
    "Iron Ore": "iron",
    "Lead-Zinc Ore": "lead_zinc",
    "Zinc": "lead_zinc",
    "Molybdenum Ore": "molybdenum",
    "Platinum Group Ore": "platinum",
    "Rare Earths Ore": "rare_earths",
    "Uranium Ore": "uranium",
    "Uranium-Vanadium Ore": "uranium",
    "Lithium Minerals": "lithium",
}
LIVE = {"Active", "Intermittent", "New Mine", "NonProducing"}


def _coord(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v else None


class MSHAMines(Source):
    info = SourceInfo(
        id="msha_mines",
        title="Mines data set (Open Government Data)",
        publisher="U.S. Department of Labor, Mine Safety and Health Administration",
        homepage="https://arlweb.msha.gov/OpenGovernmentData/OGIMSHA.asp",
        license="Public domain (U.S. Government work)",
        description="Every mine MSHA regulates, with current status, operator, primary commodity, "
        "reported employees and coordinates. Refreshed daily.",
        used_for=["Coal-mine coordinates (joined by MSHA ID)", "Metal-mine resources"],
    )

    def fetch(self, force=False):
        self.download(URL, "Mines.zip", force=force)

    def _rows(self):
        with zipfile.ZipFile(self.raw_dir / "Mines.zip") as z:
            with z.open("Mines.txt") as f:
                yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter="|")

    def process(self):
        coal, metal = {}, []
        for r in self._rows():
            lat, lon = _coord(r["LATITUDE"]), _coord(r["LONGITUDE"])
            if lat is None or lon is None:
                continue
            if lon > 0:
                lon = -lon  # a few US mines are recorded without the western sign
            if r["COAL_METAL_IND"] == "C":
                coal[r["MINE_ID"].zfill(7)] = [round(lat, 5), round(lon, 5)]
                continue
            commodity = COMMODITY.get(r["PRIMARY_SIC"].strip())
            if not commodity or r["CURRENT_MINE_STATUS"] not in LIVE:
                continue
            try:
                employees = int(float(r["NO_EMPLOYEES"]))
            except ValueError:
                employees = None
            metal.append({
                "msha_id": r["MINE_ID"].zfill(7),
                "name": " ".join(r["CURRENT_MINE_NAME"].split()),
                "commodity": commodity,
                "sic": r["PRIMARY_SIC"].strip(),
                "status": r["CURRENT_MINE_STATUS"],
                "type": r["CURRENT_MINE_TYPE"],
                "controller": " ".join(r["CURRENT_CONTROLLER_NAME"].split()),
                "operator": " ".join(r["CURRENT_OPERATOR_NAME"].split()),
                "state": r["STATE"],
                "employees": employees,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            })
        metal.sort(key=lambda m: -(m["employees"] or 0))
        retrieved = date.fromtimestamp((self.raw_dir / "Mines.zip").stat().st_mtime).isoformat()
        print(f"    {len(coal)} coal mines with coordinates, {len(metal)} live metal/strategic mines")
        return {"vintage": f"MSHA Mines data set, retrieved {retrieved}", "_files": ["Mines.zip"],
                "coal": coal, "metal": metal}
