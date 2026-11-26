"""National Historic Landmarks from the NPS National Register of Historic Places GIS layers (points + polygons).

Only rows flagged Is_NHL = 'X' are requested. Some landmarks are mapped only as polygons (e.g. the Empire State
Building), so both layers are read; a polygon landmark is placed at the mean of its outline's vertices.
NPS notes that the public GIS copy is not complete or fully up to date relative to the Register itself.
"""

from __future__ import annotations

from ..core.geo import load_geojson
from ..core.source import Source, SourceInfo
from ._resource_util import download_checked

SERVICE = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
FIELDS = "NRIS_Refnum,RESNAME,ResType,City,County,State,CertDate"
POINTS_Q = SERVICE + f"/0/query?where=Is_NHL%3D%27X%27&outFields={FIELDS}&outSR=4326&f=geojson"
POLYS_Q = (SERVICE + f"/1/query?where=Is_NHL%3D%27X%27&outFields={FIELDS}&maxAllowableOffset=0.0005"
           "&geometryPrecision=5&outSR=4326&f=geojson")


def _centre(geom):
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        return c[0], c[1]
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    pts = [p for poly in polys for p in (poly[0] if poly else [])]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


class NHLandmarks(Source):
    info = SourceInfo(
        id="nhl_landmarks",
        title="National Historic Landmarks (National Register of Historic Places GIS locations)",
        publisher="U.S. National Park Service, Cultural Resources GIS / National Register of Historic Places",
        homepage="https://www.nps.gov/subjects/nationalhistoriclandmarks/index.htm",
        license="Public domain (U.S. Government work); restricted sites are excluded by NPS",
        description="Locations of National Historic Landmarks, the nationally significant subset of the National "
        "Register, with reference number, name, type, city, county, state and certification date. The public GIS "
        "copy is incomplete; there is no measure of importance or popularity, so any landmark chosen from it is an "
        "editorial pick.",
        used_for=["Wonders: the small curated set of iconic landmarks (positions)"],
    )

    def fetch(self, force=False):
        for url, rel in ((POINTS_Q, "nhl_points.geojson"), (POLYS_Q, "nhl_polygons.geojson")):
            if not download_checked(self, url, rel, force=force, magic=b"{"):
                raise RuntimeError(f"NHL layer could not be downloaded: {url}")

    def process(self):
        out = {}
        for rel, kind in (("nhl_points.geojson", "point"), ("nhl_polygons.geojson", "polygon")):
            for f in load_geojson(self.raw_dir / rel):
                p, g = f["properties"], f.get("geometry")
                ref = (p.get("NRIS_Refnum") or "").strip()
                if not ref or not g or (ref in out and out[ref]["geometry"] == "point"):
                    continue
                c = _centre(g)
                if not c:
                    continue
                cert = (p.get("CertDate") or "").strip()
                out[ref] = {"ref": ref, "name": " ".join((p.get("RESNAME") or "").split()),
                            "type": p.get("ResType"), "city": p.get("City"), "county": p.get("County"),
                            "state": (p.get("State") or "").strip().upper() or None,
                            "designated": cert[:4] if cert[:4].isdigit() else None,
                            "lon": round(c[0], 5), "lat": round(c[1], 5), "geometry": kind}
        print(f"    {len(out)} National Historic Landmarks with a location")
        return {"vintage": "NPS National Register GIS (NHL subset)", "_files": ["nhl_points.geojson", "nhl_polygons.geojson"],
                "landmarks": out}
