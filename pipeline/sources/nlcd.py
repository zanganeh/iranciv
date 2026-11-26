"""NLCD 2021 land cover, resampled by the MRLC GeoServer to a ~2 km grid."""

from ..core.source import Source, SourceInfo

YEAR = 2021
COVERAGE = f"mrlc_download__NLCD_{YEAR}_Land_Cover_L48"
# 2418 × 1567 pixels over the CONUS extent ≈ 2.0 km pixels. Nearest neighbour keeps class codes intact.
URL = (
    "https://www.mrlc.gov/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage"
    f"&coverageId={COVERAGE}&format=text/plain&scalesize=i(2418),j(1567)"
    "&interpolation=http://www.opengis.net/def/interpolation/OGC/1/nearest-neighbor"
)
FILE = f"nlcd_{YEAR}_l48_2km.txt"

# NLCD class code → one character, so the interim stores each raster row as a short string.
LEGEND = {
    0: ".", 11: "w", 12: "i", 21: "o", 22: "l", 23: "m", 24: "h", 31: "b",
    41: "d", 42: "e", 43: "x", 52: "s", 71: "g", 81: "p", 82: "c", 90: "f", 95: "v",
}
CLASSES = {
    11: "Open water", 12: "Perennial ice/snow", 21: "Developed, open space", 22: "Developed, low intensity",
    23: "Developed, medium intensity", 24: "Developed, high intensity", 31: "Barren land",
    41: "Deciduous forest", 42: "Evergreen forest", 43: "Mixed forest", 52: "Shrub/scrub",
    71: "Grassland/herbaceous", 81: "Pasture/hay", 82: "Cultivated crops", 90: "Woody wetlands",
    95: "Emergent herbaceous wetlands",
}


class Nlcd(Source):
    info = SourceInfo(
        id="nlcd",
        title=f"National Land Cover Database {YEAR} (CONUS)",
        publisher="U.S. Geological Survey / Multi-Resolution Land Characteristics Consortium (MRLC)",
        homepage="https://www.mrlc.gov/data/nlcd-2021-land-cover-conus",
        license="Public domain (U.S. Government work). Dewitz, J., 2023, NLCD 2021 Products, doi:10.5066/P9JZ7AO3",
        description=f"30 m land cover classes for the lower 48 ({YEAR}), resampled by MRLC's web coverage service "
        "(nearest neighbour) to a ~2 km grid in NAD83 Conus Albers (EPSG:5070). Each hex's biome "
        "(forest, scrub, grassland, farmland, wetland, urban…) and its land-cover shares come from about 400 "
        "pixels sampled inside it.",
        used_for=["Hex land cover (biomes)", "Forest / cropland / developed shares"],
    )

    def fetch(self, force=False):
        self.download(URL, FILE, force=force)

    def process(self):
        lines = (self.raw_dir / FILE).read_text(encoding="latin-1").splitlines()
        affine, start = {}, None
        for k, line in enumerate(lines):
            if "elt_" in line and '"' in line:
                _, key, rest = line.split('"', 2)  # PARAMETER["elt_0_0", 1999.87],
                affine[key] = float(rest.strip(" ,]"))
            if line.strip().startswith("Band 0"):
                start = k + 1
                break
        if start is None or not {"elt_0_0", "elt_0_2", "elt_1_1", "elt_1_2"} <= affine.keys():
            raise ValueError("unexpected NLCD text header (no 'Band 0:' line or affine 'elt_' keys)")
        rows, unknown = [], set()
        for line in lines[start:]:
            vals = line.split()
            if not vals:
                continue
            row = []
            for v in vals:
                ch = LEGEND.get(int(v))
                if ch is None:
                    unknown.add(v)
                    ch = "."
                row.append(ch)
            rows.append("".join(row))
        if unknown:
            print(f"    warning: unknown NLCD codes treated as nodata: {sorted(unknown)}")
        if len({len(r) for r in rows}) != 1:
            raise ValueError("ragged NLCD rows")
        return {
            "vintage": f"NLCD {YEAR}, resampled to ~2 km",
            "_files": [FILE],
            "crs": "EPSG:5070",
            # Pixel CENTRE of column 0 / row 0 and pixel size, in metres (y size is negative: rows go south).
            "x0": affine["elt_0_2"], "dx": affine["elt_0_0"],
            "y0": affine["elt_1_2"], "dy": affine["elt_1_1"],
            "cols": len(rows[0]), "rows_n": len(rows),
            "legend": {ch: code for code, ch in LEGEND.items()},
            "classes": CLASSES,
            "rows": rows,
        }
