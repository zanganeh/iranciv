from ..core.geo import bbox_intersects, geojson_lines, geojson_rings, load_geojson
from ..core.source import Source, SourceInfo

# Pinned to a release tag so the content (and sha256) cannot change under the same URL.
TAG = "v5.1.2"
BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{TAG}/geojson"
from ..core.region import BBOX, COUNTRY_ISO3

SEA_LAKES = {"Caspian Sea"}  # drawn as sea, not lake


class NaturalEarth(Source):
    info = SourceInfo(
        id="natural_earth",
        title="Natural Earth (countries 1:50m, lakes and rivers 1:10m)",
        publisher="Natural Earth",
        homepage="https://www.naturalearthdata.com/",
        license="Public domain",
        description="Neighbouring countries (Iraq, Turkey, the Caucasus, Turkmenistan, Afghanistan, Pakistan and the "
        "Gulf states; shown fogged), large lakes (the Caspian is drawn as sea) and major rivers. "
        f"Files are pinned to the natural-earth-vector release {TAG}.",
        used_for=["Foreign land (neighbouring countries)", "Lakes", "Rivers"],
    )

    FILES = ["ne_50m_admin_0_countries", "ne_10m_lakes", "ne_10m_rivers_lake_centerlines"]

    def _path(self, name):
        return f"{TAG}/{name}.geojson"

    def fetch(self, force=False):
        for name in self.FILES:
            self.download(f"{BASE}/{name}.geojson", self._path(name), force=force)

    def _features(self, name):
        return load_geojson(self.raw_dir / self._path(name))

    def process(self):
        # Every other country with land in the window, keeping only its rings there, so foreign coasts inside the
        # map are land rather than sea.
        countries = []
        for f in self._features("ne_50m_admin_0_countries"):
            if f["properties"].get("ADM0_A3") == COUNTRY_ISO3:
                continue
            rings = [r for r in geojson_rings(f["geometry"]) if bbox_intersects(r, BBOX)]
            if rings:
                countries.append({"name": f["properties"]["ADMIN"], "rings": rings})
        lakes = []
        for f in self._features("ne_10m_lakes"):
            if f["properties"].get("name") in SEA_LAKES:
                continue
            rings = geojson_rings(f["geometry"])
            if rings and bbox_intersects(rings[0], BBOX):
                lakes.append({"name": f["properties"].get("name"), "rings": rings})
        rivers = []
        for f in self._features("ne_10m_rivers_lake_centerlines"):
            p = f["properties"]
            for line in geojson_lines(f["geometry"]):
                if bbox_intersects(line, BBOX):
                    rivers.append(
                        {"name": p.get("name"), "rank": p.get("scalerank", 10), "featurecla": p.get("featurecla"), "line": line}
                    )
        return {
            "vintage": f"Natural Earth {TAG} (1:50m countries, 1:10m lakes and rivers)",
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
        title="Natural Earth 1:10m roads, railroads and ports",
        publisher="Natural Earth",
        homepage="https://www.naturalearthdata.com/",
        license="Public domain",
        description="Freeways, major highways and railroads, as 1:10m line work, plus Natural Earth's ports. "
        "Shown as map art (roads and rail between hex centres), not as statistics: the alignment is generalised and "
        f"carries no traffic or owner data. Files are pinned to the natural-earth-vector release {TAG}.",
        used_for=["Freeways and major highways on the map", "Railroads on the map", "Ports"],
    )

    FILES = ["ne_10m_roads", "ne_10m_railroads", "ne_10m_ports"]

    def _path(self, name):
        return f"{TAG}/{name}.geojson"

    def fetch(self, force=False):
        for name in self.FILES:
            self.download(f"{BASE}/{name}.geojson", self._path(name), force=force)

    def process(self):
        roads, rail, types = [], [], {}
        for f in load_geojson(self.raw_dir / self._path("ne_10m_roads")):
            p = f["properties"]
            if p.get("featurecla") == "Ferry":  # outside the US, sov_a3 is empty: the builder clips to the country
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
            # No country field: the bbox keeps neighbours' lines too; the builder clips to the country's land.
            lines = [ln for ln in geojson_lines(f["geometry"]) if bbox_intersects(ln, BBOX)]
            if lines:
                rail.append({"scalerank": p.get("scalerank"), "category": p.get("category"),
                             "lines": [[(round(x, 4), round(y, 4)) for x, y in ln] for ln in lines]})
        ports = []
        for f in load_geojson(self.raw_dir / self._path("ne_10m_ports")):
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][:2]
            if BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]:
                ports.append({"name": p.get("name"), "scalerank": p.get("scalerank"), "website": p.get("website"),
                              "lon": round(lon, 4), "lat": round(lat, 4)})
        print(f"    {len(ports)} ports in the map window")
        print(f"    {len(roads)} road features ({types}), {len(rail)} rail features in the map window")
        return {
            "vintage": f"Natural Earth {TAG} (1:10m)",
            "_files": [self._path(n) for n in self.FILES],
            "roads": roads,
            "rail": rail,
            "ports": ports,
        }
