"""Global open databases filtered to Iran: power plants (WRI) and airports (OurAirports)."""

from __future__ import annotations

import csv

from ..core.region import BBOX, COUNTRY_ISO3
from ..core.source import Source, SourceInfo

GPPD = ("https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/"
        "global_power_plant_database.csv")
AIRPORTS = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class PowerPlants(Source):
    info = SourceInfo(
        id="wri_power_plants",
        title="Global Power Plant Database v1.3",
        publisher="World Resources Institute",
        homepage="https://datasets.wri.org/dataset/globalpowerplantdatabase",
        license="CC BY 4.0",
        description="Power stations with capacity, main fuel and location. The database was last updated in 2021, so "
        "plants built since are missing.",
        used_for=["Power plants (resources)", "Production yield", "Province energy totals"],
    )

    def fetch(self, force=False):
        self.download(GPPD, "global_power_plant_database.csv", force=force)

    def process(self):
        out = []
        with open(self.raw_dir / "global_power_plant_database.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["country"] != COUNTRY_ISO3:
                    continue
                out.append({"name": r["name"], "fuel": r["primary_fuel"], "mw": _num(r["capacity_mw"]),
                            "lon": _num(r["longitude"]), "lat": _num(r["latitude"]),
                            "year": _num(r["commissioning_year"]), "owner": r["owner"] or None})
        print(f"    {len(out)} plants, {sum(p['mw'] or 0 for p in out):,.0f} MW")
        return {"vintage": "GPPD v1.3 (2021)", "plants": out}


class Airports(Source):
    info = SourceInfo(
        id="ourairports",
        title="OurAirports",
        publisher="OurAirports (David Megginson and contributors)",
        homepage="https://ourairports.com/countries/IR/",
        license="Public domain",
        description="Airports with scheduled passenger service, by size class (large or medium). Military-only "
        "fields are left out.",
        used_for=["Airports (infrastructure)"],
    )

    def fetch(self, force=False):
        self.download(AIRPORTS, "airports.csv", force=force)

    def process(self):
        lon0, lat0, lon1, lat1 = BBOX
        out = []
        with open(self.raw_dir / "airports.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["iso_country"] != "IR" or r["type"] not in ("large_airport", "medium_airport"):
                    continue
                if r["scheduled_service"] != "yes":
                    continue
                lon, lat = _num(r["longitude_deg"]), _num(r["latitude_deg"])
                if lon is None or not (lon0 <= lon <= lon1 and lat0 <= lat <= lat1):
                    continue
                out.append({"name": r["name"], "kind": r["type"].split("_")[0], "iata": r["iata_code"] or None,
                            "city": r["municipality"] or None, "lon": lon, "lat": lat})
        print(f"    {len(out)} airports with scheduled service")
        return {"vintage": "OurAirports (retrieved at build)", "airports": out}
