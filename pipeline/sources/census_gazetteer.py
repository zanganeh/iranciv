"""Census Gazetteer: names, land areas and internal points of CBSAs, counties and places."""

import csv
import io
import zipfile

from ..core.source import Source, SourceInfo

YEAR = 2024
BASE = f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{YEAR}_Gazetteer"


class CensusGazetteer(Source):
    info = SourceInfo(
        id="census_gazetteer",
        title="Gazetteer Files (metro areas, counties, places)",
        publisher="U.S. Census Bureau",
        homepage="https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
        license="Public domain (U.S. Government work)",
        description="Names, land areas and coordinates of metro areas, counties and cities. "
        "Cities are placed at their principal city.",
        used_for=["City locations", "County land area (density)"],
    )

    FILES = {"cbsa": "cbsa_national", "counties": "counties_national", "places": "place_national"}

    def fetch(self, force=False):
        for name in self.FILES.values():
            self.download(f"{BASE}/{YEAR}_Gaz_{name}.zip", f"{YEAR}_Gaz_{name}.zip", force=force)

    def _rows(self, name):
        with zipfile.ZipFile(self.raw_dir / f"{YEAR}_Gaz_{name}.zip") as z:
            member = next(n for n in z.namelist() if n.endswith(".txt"))
            raw = z.read(member)
        try:
            text = raw.decode("utf-8-sig")  # the 2024 files are UTF-8 (Española, Cañon City)
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        header = [h.strip() for h in next(reader)]
        for row in reader:
            yield dict(zip(header, (v.strip() for v in row)))

    def process(self):
        cbsa = {
            r["GEOID"]: {
                "name": r["NAME"],
                "metro": r["CBSA_TYPE"] == "1",
                "lat": float(r["INTPTLAT"]),
                "lon": float(r["INTPTLONG"]),
            }
            for r in self._rows(self.FILES["cbsa"])
        }
        counties = {
            r["GEOID"]: {
                "name": r["NAME"],
                "usps": r["USPS"],
                "lat": float(r["INTPTLAT"]),
                "lon": float(r["INTPTLONG"]),
                "aland_km2": round(int(r["ALAND"]) / 1e6, 2),
            }
            for r in self._rows(self.FILES["counties"])
        }
        places = [
            {
                "geoid": r["GEOID"],
                "usps": r["USPS"],
                "name": r["NAME"],
                "lat": float(r["INTPTLAT"]),
                "lon": float(r["INTPTLONG"]),
                "aland_km2": round(int(r["ALAND"]) / 1e6, 2),
            }
            for r in self._rows(self.FILES["places"])
        ]
        return {
            "vintage": f"{YEAR} Gazetteer",
            "cbsa": cbsa,
            "counties": counties,
            "places": places,
            "_files": [f"{YEAR}_Gaz_{name}.zip" for name in self.FILES.values()],
        }
