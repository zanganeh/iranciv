from ..core.geo import bbox_intersects, geojson_lines, geojson_rings, load_geojson
from ..core.source import Source, SourceInfo

# Pinned to a release tag so the content (and sha256) cannot change under the same URL.
TAG = "v5.1.2"
BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{TAG}/geojson"
BBOX = (-132.0, 20.0, -60.0, 56.0)  # lon/lat window around the lower 48 and the map margin


class NaturalEarth(Source):
    info = SourceInfo(
        id="natural_earth",
        title="Natural Earth 1:50m (countries, lakes, rivers)",
        publisher="Natural Earth",
        homepage="https://www.naturalearthdata.com/",
        license="Public domain",
        description="Neighbouring countries (Canada, Mexico, Cuba, the Bahamas; shown fogged), large lakes and major rivers. "
        f"Files are pinned to the natural-earth-vector release {TAG}.",
        used_for=["Foreign land (Canada, Mexico, Caribbean)", "Lakes", "Rivers"],
    )

    FILES = ["ne_50m_admin_0_countries", "ne_50m_lakes", "ne_50m_rivers_lake_centerlines"]

    def _path(self, name):
        return f"{TAG}/{name}.geojson"

    def fetch(self, force=False):
        for name in self.FILES:
            self.download(f"{BASE}/{name}.geojson", self._path(name), force=force)

    def _features(self, name):
        return load_geojson(self.raw_dir / self._path(name))

    def process(self):
        # Every non-US country with land in the window (Canada, Mexico, Cuba, the Bahamas, Bermuda...),
        # keeping only its rings there, so foreign coasts inside the map are land rather than sea.
        countries = []
        for f in self._features("ne_50m_admin_0_countries"):
            if f["properties"].get("ADM0_A3") == "USA":
                continue
            rings = [r for r in geojson_rings(f["geometry"]) if bbox_intersects(r, BBOX)]
            if rings:
                countries.append({"name": f["properties"]["ADMIN"], "rings": rings})
        lakes = []
        for f in self._features("ne_50m_lakes"):
            rings = geojson_rings(f["geometry"])
            if rings and bbox_intersects(rings[0], BBOX):
                lakes.append({"name": f["properties"].get("name"), "rings": rings})
        rivers = []
        for f in self._features("ne_50m_rivers_lake_centerlines"):
            p = f["properties"]
            for line in geojson_lines(f["geometry"]):
                if bbox_intersects(line, BBOX):
                    rivers.append(
                        {"name": p.get("name"), "rank": p.get("scalerank", 10), "featurecla": p.get("featurecla"), "line": line}
                    )
        return {
            "vintage": f"Natural Earth {TAG} (1:50m)",
            "_files": [self._path(n) for n in self.FILES],
            "countries": countries,
            "lakes": lakes,
            "rivers": rivers,
        }


class NaturalEarthRoads(Source):
    """Natural Earth 1:10m roads and railroads (tile improvements). A separate source from the 1:50m set so the
    map's land and water (which the `world` builder reads) do not depend on these two ~45 MB files."""

    info = SourceInfo(
        id="natural_earth_roads",
        title="Natural Earth 1:10m roads and railroads",
        publisher="Natural Earth",
        homepage="https://www.naturalearthdata.com/",
        license="Public domain",
        description="Interstates, US highways and major state roads, and main railroads, as 1:10m line work. "
        "Shown as map art (roads and rail between hex centres), not as statistics: the road alignment dates from "
        "Natural Earth's older releases, carries no traffic or owner data, and the rail layer holds about half of the "
        f"real US rail network. Files are pinned to the natural-earth-vector release {TAG}.",
        used_for=["Interstates and major highways on the map", "Railroads on the map"],
    )

    FILES = ["ne_10m_roads", "ne_10m_railroads"]

    def _path(self, name):
        return f"{TAG}/{name}.geojson"

    def fetch(self, force=False):
        for name in self.FILES:
            self.download(f"{BASE}/{name}.geojson", self._path(name), force=force)

    def process(self):
        roads, rail, types = [], [], {}
        for f in load_geojson(self.raw_dir / self._path("ne_10m_roads")):
            p = f["properties"]
            if p.get("sov_a3") != "USA" or p.get("featurecla") == "Ferry":
                continue
            types[p.get("type")] = types.get(p.get("type"), 0) + 1
            if p.get("type") == "Ferry Route":
                continue
            lines = [ln for ln in geojson_lines(f["geometry"]) if bbox_intersects(ln, BBOX)]
            if not lines:
                continue
            roads.append({
                "level": p.get("level") or None,          # Interstate | Federal | State | None
                "type": p.get("type") or None,            # Major Highway, Secondary Highway, Beltway, ...
                "name": (str(p.get("name")).strip() or None) if p.get("name") not in (None, "") else None,
                "expressway": 1 if p.get("expressway") == 1 else 0,
                "scalerank": p.get("scalerank"),
                "lines": [[(round(x, 4), round(y, 4)) for x, y in ln] for ln in lines],
            })
        for f in load_geojson(self.raw_dir / self._path("ne_10m_railroads")):
            p = f["properties"]
            # No country field: the bbox keeps Canadian and Mexican lines too; the builder clips to US land.
            lines = [ln for ln in geojson_lines(f["geometry"]) if bbox_intersects(ln, BBOX)]
            if lines:
                rail.append({"scalerank": p.get("scalerank"), "category": p.get("category"),
                             "lines": [[(round(x, 4), round(y, 4)) for x, y in ln] for ln in lines]})
        print(f"    {len(roads)} US road features ({types}), {len(rail)} rail features in the map window")
        return {
            "vintage": f"Natural Earth {TAG} (1:10m)",
            "_files": [self._path(n) for n in self.FILES],
            "roads": roads,
            "rail": rail,
        }
