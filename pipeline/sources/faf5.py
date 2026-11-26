"""FHWA/BTS Freight Analysis Framework (FAF5) regional flows, aggregated to zone pairs.

The regional database is one ~700 MB CSV inside a ~190 MB zip. It is downloaded once (cached) and
streamed with zipfile + csv, never extracted to disk. Only domestic flows (trade_type 1) are kept,
for the newest year in the file, summed per unordered pair of FAF zones and per domestic mode, with
the leading commodities of each pair. Imports and exports are summed per zone.

Zones are 2017 Commodity Flow Survey areas: a metro area's part of one state (a CSA or CBSA code,
"CFSMA") or the rest of a state (CFSMA 99999). The zone's member counties are resolved here from the
crosswalk and the July 2023 OMB delineation (CSA/CBSA → counties, restricted to the zone's state);
"rest of state" zones get the state's remaining counties.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict

from ..core.source import Source, SourceInfo
from ._resource_util import xlsx_records

RELEASE = "FAF5.7.1"
YEARS = "2018-2024"
BASE = "https://faf.ornl.gov/faf5"
ZIP_URL = f"{BASE}/Data/Download_Files/{RELEASE}_{YEARS}.zip"
CROSSWALK_URL = f"{BASE}/data/CFS%20area%20code%20-%20FAF5%20zone%20id.xlsx"
METADATA_URL = f"{BASE}/data/FAF5_metadata.xlsx"
# The CFS areas are CSAs/CBSAs: their member counties come from the OMB delineation (the same file the
# cbsa_delineation source uses; that source does not keep CSA codes, so a copy is cached here).
DELINEATION_URL = ("https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/"
                   "delineation-files/list1_2023.xlsx")

MODES = {1: "truck", 2: "rail", 3: "water", 4: "air", 5: "multiple", 6: "pipeline", 7: "other"}
TOP_COMMODITIES = 5  # per zone pair

# 2017 CFS areas whose CSA/CBSA code is no longer in the 2023 delineation, mapped to today's code that
# holds the same core counties (checked against the CFS area names; see process() for the report).
CODE_PATCH: dict[str, str] = {
    "314": "315",  # Knoxville-Morristown-Sevierville, TN CSA (renumbered; 314 is now Kennewick-Richland-Walla Walla, WA)
}


class Faf5(Source):
    info = SourceInfo(
        id="faf5",
        title=f"Freight Analysis Framework ({RELEASE}, regional database {YEARS})",
        publisher="Federal Highway Administration and Bureau of Transportation Statistics (hosted by Oak Ridge National Laboratory)",
        homepage="https://faf.ornl.gov/faf5/",
        license="Public domain (U.S. Government work)",
        description="Estimated freight flows between 132 domestic FAF zones (metro areas and the rest of each state), by "
        "mode and commodity. Domestic shipments only, newest year in the file, in million current dollars and thousand "
        "short tons as FAF publishes them. These are model estimates built on the Commodity Flow Survey, not counts. A "
        "flow is assigned to its shipping and receiving zones, so it says nothing about the road it took.",
        used_for=["Trade routes (largest freight flows between metro areas in different states)",
                  "Trade partners of a city's zone"],
    )

    ZIP = f"{RELEASE}_{YEARS}.zip"
    CROSSWALK = "cfs_area_faf5_zone.xlsx"
    METADATA = "FAF5_metadata.xlsx"
    DELINEATION = "list1_2023.xlsx"

    def fetch(self, force=False):
        self.download(CROSSWALK_URL, self.CROSSWALK, force=force, expect_binary=True)
        self.download(METADATA_URL, self.METADATA, force=force, expect_binary=True)
        self.download(DELINEATION_URL, self.DELINEATION, force=force, expect_binary=True)
        self.download(ZIP_URL, self.ZIP, force=force, expect_binary=True)

    # --------------------------------------------------------------- lookups

    def _labels(self, sheet: str) -> dict[str, str]:
        out = {}
        for rec in xlsx_records(self.raw_dir / self.METADATA, "Numeric Label", sheet):
            code = (rec.get("Numeric Label") or "").strip()
            if code:
                out[code] = (rec.get("Description") or rec.get("Short Description") or "").strip()
        return out

    def _sheet_names(self) -> list[str]:
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(self.raw_dir / self.METADATA) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
        return [s.get("name") for s in wb.iter() if s.tag.endswith("}sheet")]

    def _zones(self) -> dict[str, dict]:
        zones = {}
        for rec in xlsx_records(self.raw_dir / self.CROSSWALK, "FAF"):
            faf = (rec.get("FAF") or "").strip()
            if not re.fullmatch(r"\d{3}", faf):
                continue
            zones[faf] = {
                "faf": faf,
                "state_fips": rec["STFIPS"].strip().zfill(2),
                "state": rec["STPOSTAL"].strip(),
                "cfsma": rec["CFSMA"].strip(),
                "name": re.sub(r"\s*CFS Area.*$", "", rec["CFSAREANAM"].replace("_x000D_", "").strip()),
                "short": rec.get("SHORTNAME", "").strip() or None,
            }
        return zones

    def _delineation(self):
        """(code → set of county GEOIDs) for CSA and CBSA codes, plus {code: title}."""
        members, titles = defaultdict(set), {}
        for rec in xlsx_records(self.raw_dir / self.DELINEATION, "CBSA Code"):
            st, co = rec.get("FIPS State Code", ""), rec.get("FIPS County Code", "")
            if not (st.isdigit() and co.isdigit()):
                continue
            geoid = st.zfill(2) + co.zfill(3)
            for code_key, title_key in (("CBSA Code", "CBSA Title"), ("CSA Code", "CSA Title")):
                code = (rec.get(code_key) or "").strip()
                if code:
                    members[code].add(geoid)
                    titles[code] = rec.get(title_key, "")
        return members, titles

    def _member_counties(self, zones: dict[str, dict]) -> list[str]:
        """Fill each zone's `counties` (sorted GEOIDs). Returns warnings for unmatched codes."""
        members, titles = self._delineation()
        problems = []
        taken = defaultdict(set)  # state fips → counties held by its metro zones
        for z in zones.values():
            if z["cfsma"] == "99999":
                continue
            code = CODE_PATCH.get(z["cfsma"], z["cfsma"])
            got = sorted(g for g in members.get(code, ()) if g[:2] == z["state_fips"])
            if not got:
                problems.append(f"{z['faf']} {z['name']}: CFSMA {z['cfsma']} has no 2023 counties in {z['state']}")
            z["counties"] = got
            z["delineation_title"] = titles.get(code)
            taken[z["state_fips"]].update(got)
        all_counties = defaultdict(set)
        for geoids in members.values():
            for g in geoids:
                all_counties[g[:2]].add(g)
        for z in zones.values():
            if z["cfsma"] == "99999":
                # The rest of the state: every county not in one of its metro zones. Counties outside any CBSA are
                # not in the delineation file, so the builder adds them from the map's county list (marked by None).
                z["counties"] = sorted(all_counties[z["state_fips"]] - taken[z["state_fips"]])
                z["rest_of_state"] = True
            else:
                z["rest_of_state"] = False
        return problems

    # --------------------------------------------------------------- flows

    def _stream(self):
        with zipfile.ZipFile(self.raw_dir / self.ZIP) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(name) as raw:
                yield name, csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))

    def process(self):
        zones = self._zones()
        problems = self._member_counties(zones)
        sheets = self._sheet_names()
        commodity_sheet = next((s for s in sheets if "Commodity" in s), None)
        commodities = self._labels(commodity_sheet) if commodity_sheet else {}

        pair = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))  # (a, b) → mode → [value, tons, tmiles]
        pair_sctg = defaultdict(lambda: defaultdict(float))  # (a, b) → sctg2 → value
        zone_tot = defaultdict(lambda: defaultdict(float))  # zone → key → value (million current $)
        rows = 0
        for name, reader in self._stream():
            header = next(reader)
            col = {h: i for i, h in enumerate(header)}
            year = max(int(m.group(1)) for h in header if (m := re.fullmatch(r"current_value_(\d{4})", h)))
            iv, it, im = col[f"current_value_{year}"], col[f"tons_{year}"], col[f"tmiles_{year}"]
            io_, idd, imode, isctg, itt = col["dms_orig"], col["dms_dest"], col["dms_mode"], col["sctg2"], col["trade_type"]
            for r in reader:
                rows += 1
                tt = r[itt]
                v = float(r[iv] or 0)
                o, d = r[io_].zfill(3), r[idd].zfill(3)
                if tt == "2":  # import: entered the US and was carried to dms_dest
                    zone_tot[d]["import"] += v
                    continue
                if tt == "3":  # export: carried from dms_orig to the border/port
                    zone_tot[o]["export"] += v
                    continue
                if tt != "1":
                    continue
                if o == d:
                    zone_tot[o]["within"] += v
                    continue
                zone_tot[o]["out"] += v
                zone_tot[d]["in"] += v
                key = (o, d) if o < d else (d, o)
                acc = pair[key][MODES.get(int(r[imode]), "other")]
                acc[0] += v
                acc[1] += float(r[it] or 0)
                acc[2] += float(r[im] or 0)
                pair_sctg[key][r[isctg].zfill(2)] += v
        print(f"    {rows:,} rows in {name}; {len(pair):,} domestic zone pairs, year {year}")
        if problems:
            print("    ! zones without counties: " + "; ".join(problems))

        pairs = []
        for (a, b), modes in sorted(pair.items()):
            top = sorted(pair_sctg[(a, b)].items(), key=lambda kv: -kv[1])[:TOP_COMMODITIES]
            pairs.append({
                "a": a, "b": b,
                "modes": {m: [round(v[0], 3), round(v[1], 3), round(v[2], 3)] for m, v in sorted(modes.items())},
                "commodities": [[k, round(v, 3)] for k, v in top],
            })
        used = {c for p in pairs for c, _ in p["commodities"]}
        return {
            "vintage": f"{RELEASE}, {year}",
            "_files": [self.ZIP, self.CROSSWALK, self.METADATA, self.DELINEATION],
            "release": RELEASE,
            "year": year,
            "units": {"value": "million current US dollars", "tons": "thousand short tons", "tmiles": "million ton-miles"},
            "mode_order": ["value", "tons", "tmiles"],
            "zones": dict(sorted(zones.items())),
            "zone_totals": {z: {k: round(v, 3) for k, v in sorted(t.items())} for z, t in sorted(zone_tot.items())},
            "commodities": {k: v for k, v in sorted(commodities.items()) if k.zfill(2) in used or k in used},
            "pairs": pairs,
            "problems": problems,
        }
