"""EIA tight-oil and shale-gas play boundaries (Lower 48)."""

from __future__ import annotations

from ..core.geo import geojson_rings, load_geojson
from ..core.source import Source, SourceInfo
from ._resource_util import download_checked

LAYER = ("https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services/"
         "TightOil_ShaleGas_Plays_Lower48_EIA/FeatureServer/0")
# Simplified to ~2 km (0.02°), far finer than a 25 km hex; the full set is one small request.
QUERY = LAYER + "/query?where=1%3D1&outFields=*&outSR=4326&maxAllowableOffset=0.02&f=geojson"
FILE = "shale_plays.geojson"


class EIAShalePlays(Source):
    info = SourceInfo(
        id="eia_shale_plays",
        title="Tight Oil and Shale Gas Plays, Lower 48 States",
        publisher="U.S. Energy Information Administration (U.S. Energy Atlas)",
        homepage="https://atlas.eia.gov/datasets/eia::tight-oil-and-shale-gas-plays-lower-48-states",
        license="Public domain (U.S. Government work)",
        description="Outlines of the major US shale and tight-rock plays (compiled 2015–2021), with basin and lithology. "
        "Outlines show where the producing formations lie, not production volumes.",
        used_for=["Oil and gas resources (shale plays)"],
    )

    def fetch(self, force=False):
        if not download_checked(self, QUERY, FILE, force=force, magic=b"{"):
            raise RuntimeError("shale play layer could not be downloaded")

    def process(self):
        plays = []
        for f in load_geojson(self.raw_dir / FILE):
            p = f["properties"]
            rings = geojson_rings(f["geometry"]) if f.get("geometry") else []
            if not rings:
                continue
            plays.append({
                "name": (p.get("Shale_play") or "").strip(),
                "basin": (p.get("Basin") or "").strip(),
                "lithology": (p.get("Lithology") or "").strip(),
                "age": (p.get("Age_shale") or "").strip(),
                "area_km2": p.get("Area_sq_km"),
                "rings": rings,
            })
        print(f"    {len(plays)} plays")
        return {"vintage": "EIA shale plays (boundaries 2015–2021)", "_files": [FILE], "plays": plays}
