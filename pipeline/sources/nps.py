"""NPS Land Resources Division: park unit centroids and (server-simplified) boundaries.

A live ArcGIS layer with no versioned file: the vintage is the service's dataLastEditDate, and checksums change
whenever NPS edits a boundary.
"""

from __future__ import annotations

from ..core.geo import load_geojson
from ..core.source import Source, SourceInfo
from ._resource_util import download_checked
from .mirta import layer_edit_date, polygon_area_km2, simplify_rings

SERVICE = ("https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/"
           "NPS_Land_Resources_Division_Boundary_and_Tract_Data_Service/FeatureServer")
CENTROIDS_Q = SERVICE + "/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
BOUNDS_Q = (SERVICE + "/2/query?where=1%3D1&outFields=UNIT_CODE,UNIT_NAME,UNIT_TYPE,STATE,REGION"
            "&maxAllowableOffset=0.005&geometryPrecision=4&outSR=4326&f=geojson")
RINGS_MIN_KM2 = 100  # keep outlines only for units that could cover a hex centre (a hex is ~1,600 km²)

# The boundary layer uses plural unit types ('National Parks'); the centroid layer singular ('National Park').
_PLURAL = {"National Parks": "National Park", "Parkways": "Parkway", "National Scenic Trails": "National Scenic Trail"}


def unit_type(t: str | None) -> str:
    t = " ".join((t or "").split())
    if t in _PLURAL:
        return _PLURAL[t]
    return t[:-1] if t.endswith("s") and not t.endswith("ss") else t


class NPSBoundaries(Source):
    info = SourceInfo(
        id="nps_boundaries",
        title="NPS Land Resources Division boundaries (unit centroids and boundaries)",
        publisher="U.S. National Park Service, Land Resources Division",
        homepage="https://www.arcgis.com/home/item.html?id=c8d60ffcbf5c4030a17762fe10e81c6a",
        license="Public domain (U.S. Government work)",
        description="The official outline and centre point of every National Park System unit (national parks, "
        "monuments, memorials, recreation areas, seashores, parkways ...), with its four-letter park code and unit "
        "type. A live layer: the vintage is its last edit date. Boundaries are simplified by the server to about "
        "500 m, far finer than a map hex.",
        used_for=["Wonders: national-park and park-unit positions", "Protected land under the largest parks"],
    )

    def fetch(self, force=False):
        for url, rel in ((CENTROIDS_Q, "centroids.geojson"), (BOUNDS_Q, "boundaries.geojson"),
                         (SERVICE + "/0?f=json", "layer.json")):
            if not download_checked(self, url, rel, force=force, magic=b"{"):
                raise RuntimeError(f"NPS boundary layer could not be downloaded: {url}")

    def process(self):
        bounds = {}
        for f in load_geojson(self.raw_dir / "boundaries.geojson"):
            code = (f["properties"].get("UNIT_CODE") or "").strip().upper()
            if code and f.get("geometry"):
                bounds[code] = f["geometry"]
        units = []
        for f in load_geojson(self.raw_dir / "centroids.geojson"):
            p, g = f["properties"], f.get("geometry")
            code = (p.get("UNIT_CODE") or "").strip().upper()
            if not code or not g or g.get("type") != "Point":
                continue
            lon, lat = g["coordinates"][:2]
            geom = bounds.get(code)
            area = polygon_area_km2(geom)
            units.append({
                "code": code,
                "name": " ".join((p.get("UNIT_NAME") or "").split()),
                "park": " ".join((p.get("PARKNAME") or "").split()) or None,
                "type": unit_type(p.get("UNIT_TYPE")),
                "state": (p.get("STATE") or "").strip().upper() or None,
                "region": p.get("REGION"),
                "lon": round(lon, 5), "lat": round(lat, 5),
                "area_km2": round(area, 1) if area else None,
                "rings": simplify_rings(geom, 0.005) if area and area >= RINGS_MIN_KM2 else None,
            })
        units.sort(key=lambda u: u["code"])
        edited = layer_edit_date(self.raw_dir / "layer.json")
        print(f"    {len(units)} park units ({sum(1 for u in units if u['rings'])} with outlines kept), edited {edited}")
        return {"vintage": f"NPS Land Resources Division boundaries" + (f", edited {edited}" if edited else ""),
                "_files": ["centroids.geojson", "boundaries.geojson"], "edited": edited, "units": units}
