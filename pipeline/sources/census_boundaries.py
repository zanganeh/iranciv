import zipfile

from ..core.geo import read_kml_placemarks
from ..core.source import Source, SourceInfo

YEAR = 2023
BASE = f"https://www2.census.gov/geo/tiger/GENZ{YEAR}/kml"


class CensusBoundaries(Source):
    info = SourceInfo(
        id="census_boundaries",
        title="Cartographic Boundary Files (states & counties, 1:20M)",
        publisher="U.S. Census Bureau",
        homepage="https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
        license="Public domain (U.S. Government work)",
        description="Generalized state and county outlines. Used to decide which state and county each hex belongs to.",
        used_for=["Map: land vs. water", "State borders", "County lookup for lenses"],
    )

    FILES = {"states": f"cb_{YEAR}_us_state_20m", "counties": f"cb_{YEAR}_us_county_20m"}

    def fetch(self, force=False):
        for name in self.FILES.values():
            self.download(f"{BASE}/{name}.zip", f"{name}.zip", force=force)

    def _read(self, name):
        with zipfile.ZipFile(self.raw_dir / f"{name}.zip") as z:
            with z.open(f"{name}.kml") as f:
                yield from read_kml_placemarks(f)

    def process(self):
        states = [
            {"fips": p["STATEFP"], "abbr": p["STUSPS"], "name": p["NAME"], "rings": rings}
            for p, rings in self._read(self.FILES["states"])
        ]
        counties = [
            {"geoid": p["GEOID"], "name": p.get("NAMELSAD") or p["NAME"], "state_fips": p["STATEFP"], "rings": rings}
            for p, rings in self._read(self.FILES["counties"])
        ]
        return {
            "vintage": f"{YEAR} cartographic boundaries",
            "_files": [f"{name}.zip" for name in self.FILES.values()],
            "states": states,
            "counties": counties,
        }
