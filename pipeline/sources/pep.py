"""Census Population Estimates Program (PEP): annual July 1 population, one consistent series.

Used for population growth. Unlike comparing two ACS releases, every year of one PEP vintage
is estimated with the same methods and on the same geography: the metro file uses the July
2023 OMB delineations for all years back to 2020 (including Connecticut's planning regions),
so a metro's growth never mixes two different county sets or two estimate vintages. The Census
Bureau recommends PEP rather than ACS for population change.

Two small CSV files per vintage: every metro/micro area (with its member counties) and the
nation + states. The newest vintage is discovered automatically.
"""

from __future__ import annotations

import csv
from datetime import date

from ..core.fetch import url_exists
from ..core.source import Source, SourceInfo

BASE = "https://www2.census.gov/programs-surveys/popest/datasets"


def _decade(vintage: int) -> int:
    return vintage - vintage % 10  # datasets/2020-2025/... for vintages 2020-2029


class PepPopulation(Source):
    info = SourceInfo(
        id="pep_population",
        title="Population estimates, vintage series (PEP)",
        publisher="U.S. Census Bureau, Population Estimates Program",
        homepage="https://www.census.gov/programs-surveys/popest.html",
        license="Public domain (U.S. Government work)",
        description="Annual July 1 population for the nation, every state and every metro/micro area, all "
        "years estimated together on today's metro borders (July 2023 delineations). Population growth comes "
        "from this series so that every area is compared like for like; the population figures shown in panels "
        "are the ACS 1-year totals.",
        used_for=["Population growth (cities, states, nation)"],
    )

    def _files(self, vintage: int) -> dict[str, tuple[str, str]]:
        """{kind: (url, rel_path)} for one vintage."""
        d = f"{BASE}/{_decade(vintage)}-{vintage}"
        return {
            "cbsa": (f"{d}/metro/totals/cbsa-est{vintage}-alldata.csv", f"{vintage}/cbsa-est{vintage}-alldata.csv"),
            "states": (f"{d}/state/totals/NST-EST{vintage}-ALLDATA.csv", f"{vintage}/NST-EST{vintage}-ALLDATA.csv"),
        }

    def latest_vintage(self) -> int:
        """Newest vintage whose metro file is published (the metro file comes out last, in spring)."""
        for v in range(date.today().year, date.today().year - 4, -1):
            if url_exists(self._files(v)["cbsa"][0]):
                return v
        raise RuntimeError("No PEP metro population file found")

    def _cached_vintage(self) -> int:
        """Newest vintage with both files downloaded."""
        found = sorted((int(p.name) for p in self.raw_dir.iterdir() if p.is_dir() and p.name.isdigit()),
                       reverse=True) if self.raw_dir.exists() else []
        for v in found:
            if all((self.raw_dir / rel).exists() for _, rel in self._files(v).values()):
                return v
        raise FileNotFoundError(f"{self.id}: no vintage downloaded yet (run fetch {self.id})")

    def fetch(self, force=False):
        if self.offline:  # no discovery: check that the newest cached vintage is complete
            v = self._cached_vintage()
            print(f"    PEP vintage {v} (--offline: cached)")
        else:
            v = self.latest_vintage()
            print(f"    PEP vintage {v}")
        for kind, (url, rel) in self._files(v).items():
            path = self.download(url, rel, force=force)
            with open(path, "rb") as f:
                first = f.readline()
            if not first.startswith(b"CBSA," if kind == "cbsa" else b"SUMLEV,"):
                self.discard(rel)
                raise RuntimeError(f"{url}: not the expected CSV (first line {first[:60]!r})")

    def _rows(self, rel: str):
        # The metro file is Latin-1 ("Cañon City"); the state file is ASCII.
        with open(self.raw_dir / rel, newline="", encoding="latin-1") as f:
            yield from csv.DictReader(f)

    def process(self):
        v = self._cached_vintage()
        files = self._files(v)
        years = None
        nation: list[int] = []
        states: dict[str, list[int]] = {}
        for r in self._rows(files["states"][1]):
            if years is None:
                years = sorted(int(k[11:]) for k in r if k.startswith("POPESTIMATE") and k[11:].isdigit())
            pops = [int(r[f"POPESTIMATE{y}"]) for y in years]
            if r["SUMLEV"] == "010":
                nation = pops
            elif r["SUMLEV"] == "040":
                states[r["STATE"].zfill(2)] = pops
        cbsa: dict[str, dict] = {}
        for r in self._rows(files["cbsa"][1]):
            code = r["CBSA"].strip()
            if not code.isdigit():
                continue
            if r["LSAD"].endswith("Statistical Area"):  # the area's own row
                cbsa[code] = {
                    "name": r["NAME"],
                    "pop": [int(r[f"POPESTIMATE{y}"]) for y in years],
                    "counties": [],
                }
            elif r["LSAD"] == "County or equivalent":
                cbsa.setdefault(code, {"name": None, "pop": None, "counties": []})["counties"].append(r["STCOU"].zfill(5))
        if not nation or len(states) < 51 or len(cbsa) < 900:
            raise RuntimeError(f"PEP vintage {v}: unexpected contents ({len(states)} states, {len(cbsa)} CBSAs)")
        for rec in cbsa.values():
            rec["counties"] = sorted(set(rec["counties"]))
        return {
            "vintage": f"Census population estimates, vintage {v}",
            "year": v,
            "years": years,  # each "pop" list is aligned with these (July 1 estimates)
            "nation": nation,
            "states": states,
            "cbsa": cbsa,
            "_files": [rel for _, rel in files.values()],
        }
