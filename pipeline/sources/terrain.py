"""Elevation and sea depth from the Terrarium elevation tiles (AWS Open Data "Terrain Tiles").

The tiles are web-mercator PNGs whose colour encodes height: (R * 256 + G + B / 256) - 32768 metres. They merge
SRTM, GMTED2010 and ETOPO1 (and GEBCO-style bathymetry offshore). Zoom 7 is about 1 km per pixel here; every
STRIDE-th pixel is kept (~3 km), which gives each 15 km hex about 25 samples, like ETOPO in usaciv.
Decoded with a small standard-library PNG reader.
"""

from __future__ import annotations

import math
import struct
import zlib

from ..core.region import BBOX
from ..core.source import Source, SourceInfo

ZOOM = 7
STRIDE = 3
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def tile_range(bbox=BBOX, z=ZOOM):
    lon0, lat0, lon1, lat1 = bbox
    n = 2 ** z

    def tx(lon):
        return int((lon + 180) / 360 * n)

    def ty(lat):
        r = math.radians(lat)
        return int((1 - math.asinh(math.tan(r)) / math.pi) / 2 * n)

    return range(tx(lon0), tx(lon1) + 1), range(ty(lat1), ty(lat0) + 1)


def read_png_rgb(data: bytes) -> tuple[int, int, int, bytes]:
    """Decode an 8-bit RGB/RGBA PNG → (width, height, channels, raw pixel bytes, unfiltered)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6):
                raise ValueError(f"unsupported PNG (depth {depth}, colour type {ctype})")
            ch = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(b"".join(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    for r in range(h):
        f = raw[r * (stride + 1)]
        line = bytearray(raw[r * (stride + 1) + 1:(r + 1) * (stride + 1)])
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pr) & 255
        out[r * stride:(r + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


class Terrain(Source):
    info = SourceInfo(
        id="terrain",
        title="Terrain Tiles (Terrarium encoding), zoom 7",
        publisher="Mapzen / AWS Open Data Registry (from SRTM, GMTED2010, ETOPO1 and others)",
        homepage="https://registry.opendata.aws/terrain-tiles/",
        license="Open data; see the attribution list at https://github.com/tilezen/joerd/blob/master/docs/attribution.md",
        description="Land elevation and sea depth, about 1 km per pixel, sampled every ~3 km. Each hex's relief (flat, "
        "hills, mountains, peaks) comes from the mean and roughness (standard deviation) of the land samples inside it.",
        used_for=["Relief (hills, mountains)", "Hill-shading", "Sea depth"],
    )

    def _tiles(self):
        xs, ys = tile_range()
        return [(x, y) for x in xs for y in ys]

    def fetch(self, force=False):
        for x, y in self._tiles():
            self.download(URL.format(z=ZOOM, x=x, y=y), f"z{ZOOM}/{x}_{y}.png", force=force, expect_binary=True)

    def process(self):
        lon0, lat0, lon1, lat1 = BBOX
        n = 2 ** ZOOM
        lat, lon, alt = [], [], []
        files = []
        for x, y in self._tiles():
            rel = f"z{ZOOM}/{x}_{y}.png"
            files.append(rel)
            w, h, ch, px = read_png_rgb((self.raw_dir / rel).read_bytes())
            for r in range(STRIDE // 2, h, STRIDE):
                yy = (y + (r + 0.5) / h) / n
                la = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy))))
                if not lat0 <= la <= lat1:
                    continue
                for c in range(STRIDE // 2, w, STRIDE):
                    lo = (x + (c + 0.5) / w) / n * 360 - 180
                    if not lon0 <= lo <= lon1:
                        continue
                    i = (r * w + c) * ch
                    a = px[i] * 256 + px[i + 1] + px[i + 2] / 256 - 32768
                    lat.append(round(la, 4))
                    lon.append(round(lo, 4))
                    alt.append(int(round(a)))
        print(f"    {len(alt)} samples from {len(files)} tiles")
        return {"vintage": f"Terrarium tiles, zoom {ZOOM}, every {STRIDE}rd pixel (~3 km)", "_files": files,
                "lat": lat, "lon": lon, "alt": alt}
