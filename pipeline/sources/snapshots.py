"""Hand-queried snapshots in data/manual (see data/manual/README.md).

Wikidata, Wikipedia, the World Bank and USGS are not reachable from the build machine, so their data was queried in
a browser and saved with its query and date. Each source here copies its file into data/raw (recorded in the manifest
like a download) and parses it.
"""

from __future__ import annotations

import json

from ..core.source import Source, SourceInfo

WDQS = "https://query.wikidata.org/sparql"


class _Snapshot(Source):
    FILES: dict[str, str] = {}  # manual file name -> URL it came from

    def fetch(self, force=False):
        for name, url in self.FILES.items():
            self.use_manual(name, url, force=force)

    def _load(self, name):
        with open(self.raw_dir / name, encoding="utf-8") as f:
            return json.load(f)


class WikidataPopulation(_Snapshot):
    info = SourceInfo(
        id="wikidata_population",
        title="Census populations (2006, 2011, 2016) via Wikidata",
        publisher="Statistical Centre of Iran, compiled by Wikidata",
        homepage="https://www.wikidata.org/wiki/Q794",
        license="CC0 (Wikidata)",
        description="Population claims (P1082) from the 2006, 2011 and 2016 national censuses for every province, "
        "for the counties OpenStreetMap links to Wikidata, and for cities of 30,000 or more. The 2016 census is the "
        "latest complete one; Iran's next full count has not been published.",
        used_for=["Population of provinces, counties and cities", "Growth 2006 to 2016", "Province capitals"],
    )
    FILES = {"wikidata_provinces.json": WDQS, "wikidata_county_population.json": WDQS, "wikidata_cities.json": WDQS}

    def process(self):
        prov = self._load("wikidata_provinces.json")["data"]
        counties = self._load("wikidata_county_population.json")["data"]
        cities = self._load("wikidata_cities.json")["data"]
        return {
            "vintage": "Census 2016 (Wikidata, queried 2026-11-26)",
            "provinces": {f"Q{q}": {"name": n.removesuffix(" Province"), "pop": {int(y): v for y, v in p.items()}}
                          for q, n, p in prov},
            "counties": {f"Q{q}": {"pop2016": v[0], "pop2006": v[1]} for q, v in counties.items()},
            "cities": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3], "pop": r[4],
                        "pop_year": r[5] or 2016, "pop2006": r[6] or None,
                        "capital_of": f"Q{r[7]}" if r[7] else None} for r in cities],
        }


class ProvinceGdp(_Snapshot):
    info = SourceInfo(
        id="province_gdp",
        title="Provincial GDP shares (SCI 2016) and national GDP (World Bank)",
        publisher="Statistical Centre of Iran via Persian Wikipedia; World Bank",
        homepage="https://fa.wikipedia.org/wiki/فهرست_استان‌های_ایران_بر_پایه_تولید_ناخالص_داخلی",
        license="CC BY-SA (Wikipedia table); CC BY 4.0 (World Bank)",
        description="Each province's share of national GDP in the Statistical Centre of Iran's provincial accounts "
        "for 1395 (2016/17), as tabulated on Persian Wikipedia, times the World Bank's GDP for Iran in current US "
        "dollars (NY.GDP.MKTP.CD) for 2016. 1.6% of GDP (mostly offshore oil and gas) is not assigned to any "
        "province.",
        used_for=["Gold (GDP)"],
    )
    FILES = {"province_gdp_share.json": "https://api.worldbank.org/v2/country/IRN/indicator/NY.GDP.MKTP.CD"}

    def process(self):
        d = self._load("province_gdp_share.json")
        return {"vintage": f"SCI provincial accounts {d['year']}; World Bank GDP", "year": d["year"],
                "share_pct": d["share_pct"], "extra_regional_pct": d["extra_regional_pct"],
                "national_gdp_usd": {int(y): v for y, v in d["national_gdp_usd"].items()},
                "abbr": d["province_abbr"]}


class WikidataSites(_Snapshot):
    info = SourceInfo(
        id="wikidata_sites",
        title="World Heritage sites, national parks, universities and mines via Wikidata",
        publisher="Wikidata (UNESCO, Iran Department of Environment, and others)",
        homepage="https://www.wikidata.org/",
        license="CC0 (Wikidata)",
        description="Items in Iran with the World Heritage Site designation (P1435 = Q9259) and inscription year; "
        "national parks (Q46169) with area; universities with coordinates and at least 8 Wikipedia language "
        "editions; mines with coordinates; and oil and natural gas fields with coordinates.",
        used_for=["Wonders (World Heritage, national parks, universities)", "Culture and Science yields",
                  "Mines, oil fields and gas fields"],
    )
    FILES = {"wikidata_sites.json": WDQS, "wikidata_oil_gas.json": WDQS}

    def process(self):
        d = self._load("wikidata_sites.json")
        og = self._load("wikidata_oil_gas.json")
        return {
            "vintage": "Wikidata, queried 2026-11-26",
            "whs": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3], "year": r[4]} for r in d["whs"]],
            "parks": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3], "area_ha": r[4]} for r in d["np"]],
            "universities": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3], "students": r[4],
                              "sitelinks": r[5]} for r in d["uni"]],
            "mines": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3]} for r in d["mines"]],
            "oil": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3]} for r in og["oil"]],
            "gas": [{"qid": f"Q{r[0]}", "name": r[1], "lon": r[2], "lat": r[3]} for r in og["gas"]],
        }


class UsgsQuakes(_Snapshot):
    info = SourceInfo(
        id="usgs_quakes",
        title="Earthquakes of magnitude 5.5 or more since 1900",
        publisher="U.S. Geological Survey (ComCat)",
        homepage="https://earthquake.usgs.gov/earthquakes/search/",
        license="Public domain",
        description="Every earthquake of magnitude 5.5 or more in the USGS comprehensive catalogue from 1900 to "
        "November 2026 in a box around Iran.",
        used_for=["Earthquakes lens"],
    )
    FILES = {"usgs_earthquakes.json": "https://earthquake.usgs.gov/fdsnws/event/1/query"}

    def process(self):
        d = self._load("usgs_earthquakes.json")
        return {"vintage": "USGS ComCat, 1900 to 2026-11-26",
                "quakes": [{"lon": r[0], "lat": r[1], "mag": r[2], "year": r[3]} for r in d["data"]]}
