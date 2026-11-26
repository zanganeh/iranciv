"""Provinces (ostan) and counties (shahrestan) of Iran, as OpenStreetMap boundary relations.

OSM is the only up-to-date public source of county outlines: counties are split often (about 480 today, up from
about 430 at the 2016 census). The files come from a GitHub project that exports the OSM relations to GeoJSON, pinned
to one commit so the content cannot change under the same URL.
"""

from __future__ import annotations

import json
from pathlib import Path
import math

from ..core.geo import geojson_rings
from ..core.source import Source, SourceInfo

REPO = "hosseinhabibi2004/iran-geojson"
COMMIT = "890ff6c107c54d852328bbf7f9472d146c151ebf"
BASE = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/data"
# ISO 3166-2:IR codes (the folder names of the county files).
CODES = [f"{n:02d}" for n in range(0, 31)]
SIMPLIFY_DEG = 0.004  # drop ring vertices closer than ~400 m to the previous kept one


def _simplify(ring):
    if len(ring) < 8:
        return ring
    out = [ring[0]]
    for p in ring[1:-1]:
        if math.dist(p, out[-1]) >= SIMPLIFY_DEG:
            out.append(p)
    out.append(ring[-1])
    return out if len(out) >= 4 else ring


def _en(tags: dict, suffixes: tuple[str, ...]) -> str:
    name = tags.get("name:en") or tags.get("int_name") or tags.get("name", "")
    for s in suffixes:
        if name.endswith(s):
            name = name[: -len(s)]
    return name.strip()


# OSM spellings -> the spellings used by Wikidata/Wikipedia and data/manual.
NAME_FIX = {"Chaharmahal and Bakhtiyari": "Chaharmahal and Bakhtiari",
            "Kohgiluye and Buyer Ahmad": "Kohgiluyeh and Boyer-Ahmad"}


class IranBoundaries(Source):
    info = SourceInfo(
        id="iran_boundaries",
        title="Provinces and counties of Iran (OpenStreetMap boundary relations)",
        publisher="OpenStreetMap contributors, exported to GeoJSON by the iran-geojson project",
        homepage=f"https://github.com/{REPO}",
        license="ODbL 1.0 (OpenStreetMap data); export scripts MIT",
        description="Outlines of Iran's 31 provinces (OSM admin_level 4) and their counties (admin_level 5), with "
        "English and Persian names and Wikidata ids. Used to decide which province and county each hex belongs to. "
        f"Pinned to commit {COMMIT[:10]}.",
        used_for=["Map: land vs. water", "Province borders", "County lookup for lenses and the Explorer"],
    )

    def _files(self):
        yield "provinces/provinces.all.min.geojson"
        for c in CODES:
            yield f"counties/IR-{c}/IR-{c}.all.min.geojson"

    def fetch(self, force=False):
        for rel in self._files():
            self.download(f"{BASE}/{rel}", f"{COMMIT[:10]}/{rel}", force=force)

    def _load(self, rel):
        with open(self.raw_dir / f"{COMMIT[:10]}/{rel}", encoding="utf-8") as f:
            return json.load(f)["features"]

    def process(self):
        manual = Path(__file__).resolve().parents[2] / "data" / "manual" / "province_gdp_share.json"
        abbr = json.loads(manual.read_text(encoding="utf-8"))["province_abbr"]
        states = []
        for f in self._load("provinces/provinces.all.min.geojson"):
            t = f["properties"].get("tags", f["properties"])
            iso = t.get("ISO3166-2", "")
            if not iso.startswith("IR-") or not f.get("geometry"):
                continue
            states.append({
                "fips": iso[3:], "iso": iso, "name": _en(t, (" Province",)), "name_fa": t.get("name:fa"),
                "abbr": None, "wikidata": t.get("wikidata"),
                "rings": [_simplify(r) for r in geojson_rings(f["geometry"])],
            })
        counties = []
        for c in CODES:
            feats = [f for f in self._load(f"counties/IR-{c}/IR-{c}.all.min.geojson")
                     if f.get("geometry") and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
            feats.sort(key=lambda f: _en(f["properties"].get("tags", {}), (" County",)))
            for k, f in enumerate(feats, 1):
                t = f["properties"].get("tags", {})
                counties.append({
                    "geoid": f"{c}{k:03d}", "name": _en(t, ()) or f"County {c}{k:03d}", "name_fa": t.get("name:fa"),
                    "state_fips": c, "wikidata": t.get("wikidata"), "osm": f["properties"].get("id"),
                    "rings": [_simplify(r) for r in geojson_rings(f["geometry"])],
                })
        for s in states:
            s["name"] = NAME_FIX.get(s["name"], s["name"])
            s["abbr"] = abbr.get(s["name"]) or s["fips"]
        missing = [s["name"] for s in states if s["name"] not in abbr]
        if missing:
            print(f"    no abbreviation for: {missing}")
        print(f"    {len(states)} provinces, {len(counties)} counties")
        return {
            "vintage": f"OpenStreetMap via iran-geojson @ {COMMIT[:10]}",
            "_files": [f"{COMMIT[:10]}/{rel}" for rel in self._files()],
            "states": sorted(states, key=lambda s: s["name"]),
            "counties": counties,
        }
