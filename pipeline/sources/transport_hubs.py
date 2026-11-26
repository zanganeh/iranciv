"""Transport hubs as tile improvements: USACE principal ports (tonnage) and FAA airports (boardings, cargo).

Two sources:

* `usace_ports`  – USACE Waterborne Commerce principal ports (BTS NTAD layer), the port-area polygons'
                   centroids with total/domestic/foreign/import/export short tons.
* `faa_airports` – FAA calendar-year passenger boardings (enplanements) and all-cargo landed weight, placed
                   with the NTAD Aviation Facilities layer (FAA NASR airport master: ARPT_ID = the FAA Locid).

Both ArcGIS REST layers are keyless but not version-pinned, so the sha256 in the manifest is what pins a run.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked, num, xlsx_records

USACE_LAYER = "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Principal_Ports/FeatureServer/0/query"
NTAD_AIRPORTS = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Aviation_Facilities/"
                 "FeatureServer/0/query")
FAA_PAGE = "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger"
FAA_FILES = {  # kind → file-name pattern on the FAA page (the case of "arp-cy" changes between years)
    "enplanements": r"arp-cy(\d{4})-commercial-service-enplanements\.xlsx",
    "cargo": r"arp-cy(\d{4})-all-cargo-airports\.xlsx",
}
PORT_TYPES = {"coastal": "coastal", "great lakes": "great_lakes", "internal": "inland", "inland": "inland"}


def _query(base: str, **params) -> str:
    return base + "?" + urllib.parse.urlencode(params)


class UsacePorts(Source):
    info = SourceInfo(
        id="usace_ports",
        title="Principal Ports of the United States (tonnage)",
        publisher="U.S. Army Corps of Engineers, Waterborne Commerce Statistics Center (via BTS National Transportation Atlas Database)",
        homepage="https://doi.org/10.21949/1529073",
        license="Public domain (U.S. Government work; no use restrictions)",
        description="The 150 principal US ports ranked by total cargo tonnage (short tons), with domestic, foreign, "
        "import and export tonnage. Each port is a port statistical area, often miles of waterfront along a river, so "
        "its map position is the area's centroid and only approximate for long river ports.",
        used_for=["Ports on the map (harbour icons sized by tonnage)"],
    )

    FILE = "principal_ports.json"
    URL = _query(USACE_LAYER, where="1=1", outFields="*", outSR="4326", returnGeometry="false",
                 returnCentroid="true", f="json")

    LAYER = "principal_ports_layer.json"

    def fetch(self, force=False):
        self.download(self.URL, self.FILE, force=force)
        self.download(USACE_LAYER.rsplit("/", 1)[0] + "?f=json", self.LAYER, force=force)

    def process(self):
        doc = json.loads((self.raw_dir / self.FILE).read_text())
        if "features" not in doc:
            raise ValueError(f"unexpected USACE response: {str(doc)[:200]}")
        ports = []
        for f in doc["features"]:
            a, c = f.get("attributes", {}), f.get("centroid") or {}
            name = (a.get("PORTNAME") or "").strip()
            m = re.search(r",\s*([A-Z]{2})(?=,|$)", name)
            state = m.group(1) if m else None
            if m:
                name = (name[:m.start()] + name[m.end():]).strip(" ,")
            if name.endswith(", Port of"):
                name = "Port of " + name[: -len(", Port of")]
            # Tonnage fields are short tons; the year is in the layer description (CY2023 as of 2026).
            ports.append({
                "id": str(a.get("PORT")).strip(),
                "name": name,
                "state": state,
                "type": PORT_TYPES.get((a.get("TYPE") or "").strip().lower()),
                "rank": a.get("RANK"),
                "tons": a.get("TOTAL"),
                "domestic": a.get("DOMESTIC"),
                "foreign": a.get("FOREIGN_"),
                "imports": a.get("IMPORTS"),
                "exports": a.get("EXPORTS"),
                "lon": round(c["x"], 5) if c.get("x") is not None else None,
                "lat": round(c["y"], 5) if c.get("y") is not None else None,
            })
        ports.sort(key=lambda p: (p["rank"] is None, p["rank"] or 0))
        # The rows carry no year: it is in the layer's description ("... calendar year 2023 ...").
        layer = json.loads((self.raw_dir / self.LAYER).read_text())
        text = " ".join(str(layer.get(k) or "") for k in ("description", "name", "copyrightText"))
        years = [int(y) for y in re.findall(r"(?:CY|[Cc]alendar [Yy]ear|year)\s*(20\d\d)", text)]
        year = max(years) if years else None
        if year is None:
            print("    ! tonnage year not found in the layer description")
        return {"vintage": f"USACE principal ports, CY{year} tonnage" if year else "USACE principal ports",
                "_files": [self.FILE, self.LAYER], "year": year,
                "ports": ports}


class FaaAirports(Source):
    info = SourceInfo(
        id="faa_airports",
        title="FAA passenger boardings and all-cargo data, with NASR airport locations",
        publisher="Federal Aviation Administration (Office of Airports; NASR airport master via BTS NTAD)",
        homepage=FAA_PAGE,
        license="Public domain (U.S. Government work)",
        description="Calendar-year passenger boardings (enplanements) at every commercial-service airport, with the "
        "previous year's figure and the FAA hub class (large, medium, small, nonhub), plus landed weight of all-cargo "
        "aircraft (pounds) at the busiest cargo airports. FAA's tables have no coordinates, so airports are placed by "
        "their FAA location id in the NTAD Aviation Facilities layer (certificated airports).",
        used_for=["Airports on the map (runway icons sized by hub class)"],
    )

    COORDS = "ntad_aviation_facilities.geojson"
    EXTRA = "ntad_aviation_facilities_extra.geojson"

    def _cached(self, kind: str):
        found = []
        for p in self.raw_dir.glob("*.xlsx"):
            if m := re.fullmatch(FAA_FILES[kind], p.name.lower()):
                found.append((int(m.group(1)), p))
        return max(found) if found else None

    def _coords_url(self, ids: list[str] | None = None, only: bool = False) -> str:
        # Certificated airports (the field holds '' rather than NULL), or the listed FAA ids.
        where = "ARPT_ID IN (" + ",".join(f"'{i}'" for i in ids) + ")" if only else "FAR_139_TYPE_CODE <> ''"
        return _query(NTAD_AIRPORTS, where=where,
                      outFields="ARPT_ID,ICAO_ID,ARPT_NAME,CITY,STATE_CODE,COUNTY_NAME,SITE_TYPE_CODE,"
                                "FAR_139_TYPE_CODE,LAT_DECIMAL,LONG_DECIMAL,EFF_DATE",
                      outSR="4326", f="geojson")

    def fetch(self, force=False):
        if force or not (self._cached("enplanements") and self._cached("cargo")):
            links = {}
            page = download_checked(self, FAA_PAGE, "index.html", force=True, skip_errors=True)
            if page:
                for href in re.findall(r'href="([^"]+\.xlsx)"', page.read_text(errors="replace"), re.I):
                    for kind, pat in FAA_FILES.items():
                        if m := re.search(pat, href.lower()):
                            url = urllib.parse.urljoin("https://www.faa.gov", href)
                            if kind not in links or int(m.group(1)) > links[kind][0]:
                                links[kind] = (int(m.group(1)), url, href.rsplit("/", 1)[-1])
            for kind in FAA_FILES:
                if kind in links:
                    year, url, name = links[kind]
                    cands = [(url, name)]
                else:  # page unavailable: guess the last few years' names
                    stem = "commercial-service-enplanements" if kind == "enplanements" else "all-cargo-airports"
                    cands = [(f"{FAA_PAGE}/arp-cy{y}-{stem}.xlsx", f"arp-cy{y}-{stem}.xlsx")
                             for y in range(date.today().year - 1, date.today().year - 4, -1)]
                for url, name in cands:
                    if download_checked(self, url, name, force=force, magic=b"PK", binary=True, skip_errors=True):
                        break
                else:
                    raise RuntimeError(f"no FAA {kind} workbook could be downloaded")
        coords = self.download(self._coords_url(), self.COORDS, force=force)
        # Airports in FAA's tables that are not certificated (small commuter fields) are looked up by id.
        have = {f["properties"].get("ARPT_ID") for f in json.loads(coords.read_text()).get("features", [])}
        want = set()
        for kind in FAA_FILES:
            if cached := self._cached(kind):
                want |= {r.get("Locid", "").strip() for r in xlsx_records(cached[1], "Locid") if num(r.get("Rank"))}
        extra = sorted(w for w in want - have if re.fullmatch(r"[A-Z0-9]{3,4}", w))
        if extra:
            self.download(self._coords_url(extra, only=True), self.EXTRA, force=force)

    def _table(self, kind: str, value_col: str, prev_col: str):
        cached = self._cached(kind)
        if not cached:
            raise FileNotFoundError(f"no FAA {kind} workbook – run: uv run python -m pipeline fetch faa_airports")
        year, path = cached
        rows = {}
        for rec in xlsx_records(path, "Locid"):
            rank = num(rec.get("Rank"))
            loc = (rec.get("Locid") or "").strip()
            if rank is None or not loc:  # trailer and total rows have no numeric rank
                continue
            keys = {k.replace("\n", " ").strip(): v for k, v in rec.items()}
            val = next((num(v) for k, v in keys.items() if re.fullmatch(value_col.format(yy=year % 100, y=year), k)), None)
            prev = next((num(v) for k, v in keys.items()
                         if re.fullmatch(prev_col.format(yy=(year - 1) % 100, y=year - 1), k)), None)
            rows[loc] = {"rank": int(rank), "state": keys.get("ST"), "name": keys.get("Airport Name"),
                         "city": keys.get("City"), "level": keys.get("S/L") or keys.get("Svc Lvl"),
                         "hub": (keys.get("Hub") or "").strip() or None, "value": val, "prev": prev}
        return year, path, rows

    def process(self):
        e_year, e_path, enpl = self._table("enplanements", r"CY {yy} Enplanements", r"CY {yy} Enplanements")
        c_year, c_path, cargo = self._table("cargo", r"{y} Landed Weight \(lbs\.\)", r"{y} Landed Weight \(lbs\.\)")
        files = [self.COORDS] + ([self.EXTRA] if (self.raw_dir / self.EXTRA).exists() else [])
        feats = [f for name in files for f in json.loads((self.raw_dir / name).read_text()).get("features", [])]
        where = {}
        for f in feats:
            p = f["properties"]
            lat, lon = num(p.get("LAT_DECIMAL")), num(p.get("LONG_DECIMAL"))
            if p.get("ARPT_ID") and lat is not None and lon is not None:
                where[p["ARPT_ID"].strip()] = {"lat": round(lat, 5), "lon": round(lon, 5),
                                              "faa_name": p.get("ARPT_NAME"), "eff": p.get("EFF_DATE")}
        airports, missing = [], []
        for loc in sorted(set(enpl) | set(cargo)):
            e, c, w = enpl.get(loc), cargo.get(loc), where.get(loc)
            if w is None:
                missing.append(loc)
            base = e or c
            hub = (e or {}).get("hub")
            airports.append({
                "id": loc,
                "name": base["name"],
                "city": base["city"],
                "state": base["state"],
                "level": (e or {}).get("level"),            # P primary, CS non-primary commercial service, ...
                "hub": hub if hub in ("L", "M", "S", "N") else None,
                "enplanements": e["value"] if e else None,
                "enplanements_prev": e["prev"] if e else None,
                "enplanements_rank": e["rank"] if e else None,
                "cargo_lbs": c["value"] if c else None,     # all-cargo landed weight (only the cargo table's airports)
                "cargo_lbs_prev": c["prev"] if c else None,
                "cargo_rank": c["rank"] if c else None,
                "lat": w["lat"] if w else None,
                "lon": w["lon"] if w else None,
            })
        print(f"    {len(enpl)} airports with boardings (CY{e_year}), {len(cargo)} cargo airports (CY{c_year}); "
              f"{len(missing)} without NTAD coordinates")
        effs = sorted({w["eff"] for w in where.values() if w.get("eff")})
        return {
            "vintage": f"FAA CY{e_year} enplanements, CY{c_year} all-cargo",
            "_files": [e_path.name, c_path.name, *files],
            "year": e_year, "cargo_year": c_year,
            "nasr_effective": effs[-1] if effs else None,
            "airports": airports,
            "missing_coords": missing,
        }
