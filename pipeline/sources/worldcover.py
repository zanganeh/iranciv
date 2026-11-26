"""ESA WorldCover 2021 land cover, resampled to a 0.01° (~1 km) lon/lat grid over the map window.

WorldCover is 10 m, served as 3°×3° cloud-optimised GeoTIFFs. `fetch` reads each tile's internal overview at the
target resolution (nearest neighbour, so class codes stay intact) and writes one small text raster: one character per
pixel, one line per row. That step needs `rasterio` (pip install rasterio); `process` and everything after it are
standard-library only, like the rest of the pipeline.

Classes are translated to the NLCD-style codes the world builder already understands (docs/DATA_CONTRACTS.md), so a
hex's biome rule is the same as in usaciv: tree cover → forest (41), shrubland → scrub & desert (52), grassland and
moss → grassland (71), cropland → cropland (82), built-up → developed (23), bare → barren (31), snow → ice (12),
water → open water (11), herbaceous wetland → wetland (95), mangroves → woody wetland (90).
"""

from __future__ import annotations

import os

from ..core import fetch as core_fetch
from ..core.region import BBOX
from ..core.source import Source, SourceInfo

STEP = 0.01  # degrees per output pixel
TILE_DEG = 3
URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
FILE = "worldcover_2021_0.01deg.txt"

WC_TO_NLCD = {10: 41, 20: 52, 30: 71, 40: 82, 50: 23, 60: 31, 70: 12, 80: 11, 90: 95, 95: 90, 100: 71, 0: 0}
LEGEND = {0: ".", 11: "w", 12: "i", 23: "m", 31: "b", 41: "d", 52: "s", 71: "g", 82: "c", 90: "f", 95: "v"}
CLASSES = {10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "Built-up",
           60: "Bare / sparse vegetation", 70: "Snow and ice", 80: "Permanent water bodies",
           90: "Herbaceous wetland", 95: "Mangroves", 100: "Moss and lichen"}


def _tile_name(lat: int, lon: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def _window():
    lon0, lat0, lon1, lat1 = BBOX
    x0 = int(lon0 // TILE_DEG * TILE_DEG)
    y0 = int(lat0 // TILE_DEG * TILE_DEG)
    return x0, y0, int(-(-lon1 // TILE_DEG) * TILE_DEG), int(-(-lat1 // TILE_DEG) * TILE_DEG)


class WorldCover(Source):
    info = SourceInfo(
        id="worldcover",
        title="ESA WorldCover 10 m 2021 (v200)",
        publisher="European Space Agency (ESA WorldCover consortium)",
        homepage="https://esa-worldcover.org/",
        license="CC BY 4.0. © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
        "processed by ESA WorldCover consortium",
        description="Global 10 m land cover from Sentinel-1 and -2 (2021), resampled (nearest neighbour) to a ~1 km "
        "grid. Each hex's biome (forest, scrub and desert, grassland, cropland, wetland, urban, barren, ice) and its "
        "land-cover shares come from about 580 pixels inside it.",
        used_for=["Hex land cover (biomes)", "Forest / cropland / developed shares"],
    )

    def fetch(self, force=False):
        dest = self.raw_dir / FILE
        if dest.exists() and not force:
            return
        if core_fetch.OFFLINE:
            raise core_fetch.OfflineError(f"--offline: {self.id}/{FILE} is not cached")
        try:
            import numpy as np
            import rasterio
            from rasterio.enums import Resampling
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("worldcover fetch needs rasterio and numpy: pip install rasterio") from exc
        os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
        ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if ca:
            os.environ.setdefault("GDAL_CURL_CA_BUNDLE", ca)
        x0, y0, x1, y1 = _window()
        per = round(TILE_DEG / STEP)
        cols, rows = round((x1 - x0) / STEP), round((y1 - y0) / STEP)
        grid = np.zeros((rows, cols), dtype=np.uint8)
        lut = np.zeros(256, dtype=np.uint8)
        for k, v in WC_TO_NLCD.items():
            lut[k] = v
        used = []
        for lat in range(y0, y1, TILE_DEG):
            for lon in range(x0, x1, TILE_DEG):
                url = URL.format(tile=_tile_name(lat, lon))
                try:
                    with rasterio.open("/vsicurl/" + url) as d:
                        a = d.read(1, out_shape=(per, per), resampling=Resampling.nearest)
                except rasterio.errors.RasterioIOError:
                    continue  # open sea: no tile
                used.append(url)
                r = (y1 - lat - TILE_DEG) * per // TILE_DEG
                c = (lon - x0) * per // TILE_DEG
                grid[r:r + per, c:c + per] = lut[a]
                print(f"    ↓ {url}")
        chars = np.array([ord(LEGEND.get(i, ".")) for i in range(256)], dtype=np.uint8)
        text = "\n".join(chars[row].tobytes().decode("ascii") for row in grid)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# lon0={x0} lat1={y1} step={STEP} cols={cols} rows={rows}\n" + text + "\n")
        core_fetch.record(self.id, FILE, URL.format(tile="<tile>"), tiles=len(used),
                          note="resampled to 0.01° by nearest neighbour from the tiles' internal overviews")

    def process(self):
        lines = (self.raw_dir / FILE).read_text().splitlines()
        head = dict(kv.split("=") for kv in lines[0][2:].split())
        rows = lines[1:]
        step = float(head["step"])
        return {
            "vintage": "ESA WorldCover 2021 v200, resampled to 0.01°",
            "_files": [FILE],
            "crs": "EPSG:4326",
            # Pixel CENTRE of column 0 / row 0 and pixel size in degrees (rows go south).
            "x0": float(head["lon0"]) + step / 2, "dx": step,
            "y0": float(head["lat1"]) - step / 2, "dy": -step,
            "cols": int(head["cols"]), "rows_n": len(rows),
            "legend": {ch: code for code, ch in LEGEND.items()},
            "classes": CLASSES,
            "rows": rows,
        }
