"""DoD MIRTA: military installations, ranges and training areas (points and boundaries).

Two ArcGIS FeatureServer layers from the same Esri federal-data organisation (one request each, all features under
the server's 2,000-record limit): the installation points (FY2025 MIRTA) and the boundary polygons. MIRTA has no
county, size or headcount field: area comes from the polygons (equal-area projection) and the builder finds the
county and hex.

Also home to two small geometry helpers (`polygon_area_km2`, `simplify_rings`) shared with the NPS boundary source.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from ..core.geo import Albers, load_geojson
from ..core.source import Source, SourceInfo
from ._resource_util import download_checked

HOST = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
POINTS = HOST + "/MIRTA_Points_A_view/FeatureServer/0"
POLYGONS = HOST + "/MIRTA_Polygons_A_view/FeatureServer/0"
# Server-side generalisation times out (HTTP 504), so the polygons come in full at ~10 m precision (~14 MB once).
POINTS_Q = POINTS + "/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
POLY_Q = POLYGONS + "/query?where=1%3D1&outFields=*&geometryPrecision=4&outSR=4326&f=geojson"
SIMPLIFY_DEG = 0.004  # ~400 m: far finer than a 25 km hex

_ALBERS = Albers()


# --- geometry helpers (also used by nps.py) ---------------------------------------


def _ring_area(ring) -> float:
    pts = [_ALBERS(lon, lat) for lon, lat in ring]
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def polygon_area_km2(geom: dict | None) -> float | None:
    """Area of a GeoJSON (Multi)Polygon in km² (Albers equal-area; first ring of each polygon is the outer ring,
    the rest are holes)."""
    if not geom:
        return None
    t, c = geom.get("type"), geom.get("coordinates")
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    total = 0.0
    for poly in polys:
        if not poly:
            continue
        total += _ring_area(poly[0]) - sum(_ring_area(h) for h in poly[1:])
    return total if polys else None


def _dp(pts, tol):
    """Douglas-Peucker (iterative) on a list of (x, y)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x0, y0), (x1, y1) = pts[a], pts[b]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - x0) - dx * (py - y0)) / norm if norm else math.hypot(px - x0, py - y0)
            if d > best:
                best, idx = d, i
        if idx > 0 and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def simplify_rings(geom: dict | None, tol: float = SIMPLIFY_DEG) -> list[list[list[float]]]:
    """All rings of a GeoJSON (Multi)Polygon, simplified to `tol` degrees and rounded to 4 decimals. Rings that
    collapse below 4 points are dropped (they are far smaller than a hex). Even-odd rings, like core.geo.Shape."""
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates")
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    out = []
    for poly in polys:
        for ring in poly:
            pts = [(float(p[0]), float(p[1])) for p in ring]
            s = _dp(pts, tol)
            if len(s) >= 4:
                out.append([[round(x, 4), round(y, 4)] for x, y in s])
    return out


def layer_edit_date(path) -> str | None:
    """dataLastEditDate (ISO date) from an ArcGIS layer's ?f=json description, if present."""
    try:
        meta = json.loads(path.read_text())
        ms = (meta.get("editingInfo") or {}).get("dataLastEditDate")
        return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat() if ms else None
    except (OSError, ValueError, TypeError):
        return None


# --- source -----------------------------------------------------------------------------


def _p(props: dict, key: str):
    """Property by case-insensitive name (the polygon layer uses upper-case field names)."""
    if key in props:
        return props[key]
    lk = key.lower()
    return next((v for k, v in props.items() if k.lower() == lk), None)


# siteReportingComponent → component id. The point layer uses codes ('usa', 'armyNationalGuard'), the polygon
# layer labels ('USA', 'Army National Guard'); both are matched case- and space-insensitively.
COMPONENTS = {
    "usa": "army", "usn": "navy", "usaf": "air_force", "usmc": "marines",
    "usar": "army_reserve", "usnr": "navy_reserve", "afr": "air_force_reserve", "usmcr": "marine_reserve",
    "armynationalguard": "army_guard", "airnationalguard": "air_guard",
}


def component_id(raw: str) -> str:
    """'usa' / 'USA' → 'army'; anything unlisted (WHS, other) → 'other'."""
    return COMPONENTS.get("".join(raw.lower().split()), "other")


def _text(v) -> str:
    return " ".join(str(v).split()) if v not in (None, "") else ""


class Mirta(Source):
    info = SourceInfo(
        id="mirta",
        title="Military Installations, Ranges and Training Areas (MIRTA), FY2025",
        publisher="U.S. Department of Defense, OASD(EI&E) Defense Installation Spatial Data Infrastructure (DISDI); "
        "hosted by Esri's U.S. federal data ArcGIS organisation",
        homepage="https://www.acq.osd.mil/eie/imr/rpid/disdi/",
        license="U.S. Government work (the Esri-hosted copy states CC BY 4.0); DoD gives no warranty and does not "
        "list every DoD site",
        description="DoD's inventory of military installations, ranges and training areas: one point per site and "
        "the site boundaries, with reporting component (Army, Navy, Air Force, Marines, their reserves, Army and Air "
        "National Guard), operational status and a joint-base flag. Space Force bases are filed under the Air Force; "
        "Coast Guard (DHS) sites are not included. MIRTA has no headcount or acreage: areas are computed from the "
        "boundaries, and no per-base personnel figure exists in public data.",
        used_for=["Military layer: forts and bases on the map", "Restricted ground (the largest ranges)",
                  "Encampment district rule for cities"],
    )

    def fetch(self, force=False):
        for url, rel in ((POINTS_Q, "points.geojson"), (POLY_Q, "polygons.geojson"),
                         (POINTS + "?f=json", "points_layer.json"), (POLYGONS + "?f=json", "polygons_layer.json")):
            if not download_checked(self, url, rel, force=force, magic=b"{"):
                raise RuntimeError(f"MIRTA layer could not be downloaded: {url}")

    def process(self):
        points = []
        for f in load_geojson(self.raw_dir / "points.geojson"):
            pr, g = f["properties"], f.get("geometry")
            if not g or g.get("type") != "Point":
                continue
            lon, lat = g["coordinates"][:2]
            points.append({
                "site": _text(_p(pr, "siteName")),
                "feature": _text(_p(pr, "featureName")),
                "component": component_id(_text(_p(pr, "siteReportingComponent"))),
                "component_raw": _text(_p(pr, "siteReportingComponent")),
                "status": _text(_p(pr, "siteOperationalStatus")).lower(),
                "joint": _text(_p(pr, "isJointBase")).lower() == "yes",
                "state": _text(_p(pr, "stateNameCode")).upper(),
                "lon": round(lon, 5), "lat": round(lat, 5),
            })
        polygons = []
        for f in load_geojson(self.raw_dir / "polygons.geojson"):
            pr, g = f["properties"], f.get("geometry")
            area = polygon_area_km2(g)
            rings = simplify_rings(g)
            if not area:
                continue
            polygons.append({
                "site": _text(_p(pr, "siteName")),
                "feature": _text(_p(pr, "featureName")),
                "component": component_id(_text(_p(pr, "siteReportingComponent"))),
                "component_raw": _text(_p(pr, "siteReportingComponent")),
                "status": _text(_p(pr, "siteOperationalStatus")).lower(),
                "joint": _text(_p(pr, "isJointBase")).lower() == "yes",
                "state": _text(_p(pr, "stateNameCode")).upper(),
                "area_km2": round(area, 2),
                "rings": rings,
            })
        polygons.sort(key=lambda p: -p["area_km2"])
        edited = {k: layer_edit_date(self.raw_dir / f"{k}_layer.json") for k in ("points", "polygons")}
        print(f"    {len(points)} installation points, {len(polygons)} boundary polygons "
              f"(largest: {polygons[0]['site']} {polygons[0]['area_km2']:,.0f} km²)")
        vintage = "DoD MIRTA FY2025" + (f" (layer edited {edited['points']})" if edited["points"] else "")
        return {"vintage": vintage, "_files": ["points.geojson", "polygons.geojson"], "edited": edited,
                "points": points, "polygons": polygons}
