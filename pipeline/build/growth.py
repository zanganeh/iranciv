"""Population growth from the Census population estimates (PEP), one consistent series.

Helper module (no build()): used by cities.py and states.py.

growth = population(end) / population(BASELINE_YEAR) - 1, over growth_years = [BASELINE_YEAR, end],
where `end` is the ACS year the panels show (nation.json "year"). Both numbers come from the same
PEP vintage, which estimates every year on the current (July 2023) metro delineations. So, unlike a
comparison of two ACS releases, there is no border change between the two years (Chicago losing
Kenosha County, Connecticut's planning regions, renamed areas such as Kiryas Joel-Poughkeepsie-
Newburgh) and no mix of estimate vintages: every area is compared like for like.

A CBSA's growth is still null (never a guess) when:

* it is not in the PEP metro file;
* the PEP file's member counties differ from the OMB delineation the rest of the map uses (a guard
  for a future delineation update arriving in one file before the other);
* |growth| > MAX_GROWTH (a cap kept as a last sanity check; no US metro gained or lost a quarter of
  its people in four years).
"""

from __future__ import annotations

MAX_GROWTH = 0.25
BASELINE_YEAR = 2021  # a ~4-year span ending at the ACS year shown in panels


def rate(latest: float | None, earlier: float | None) -> float | None:
    """latest / earlier - 1, rounded; None if either is missing or |rate| > MAX_GROWTH."""
    if not latest or not earlier:
        return None
    g = latest / earlier - 1
    return round(g, 4) if abs(g) <= MAX_GROWTH else None


class Growth:
    """Growth rates from the processed pep_population interim, for the window ending at `end_year`."""

    def __init__(self, pep: dict | None, end_year: int):
        self.pep = pep
        years = pep["years"] if pep else []
        end = end_year if end_year in years else (max(years) if years else None)
        if pep and end != end_year:
            print(f"    PEP vintage {pep['year']} has no {end_year} estimate: growth ends in {end} "
                  f"(check will flag this; fetch a newer pep_population)")
        ok = pep is not None and end is not None and BASELINE_YEAR in years and end > BASELINE_YEAR
        self.years = [BASELINE_YEAR, end] if ok else None
        self._i = (years.index(BASELINE_YEAR), years.index(end)) if ok else None

    def _window(self, pops: list | None) -> float | None:
        if not pops or not self._i:
            return None
        return rate(pops[self._i[1]], pops[self._i[0]])

    def nation(self) -> float | None:
        return self._window(self.pep["nation"]) if self.pep else None

    def state(self, fips: str) -> float | None:
        return self._window(self.pep["states"].get(fips)) if self.pep else None

    def cbsa(self, code: str, members: list[str] | None) -> tuple[float | None, str | None]:
        """(growth or None, reason when None). `members` are the delineation's county FIPS."""
        if not self.years:
            return None, "no population estimates"
        rec = self.pep["cbsa"].get(code)
        if not rec or not rec.get("pop"):
            return None, "not in population estimates"
        if members is not None and set(members) != set(rec["counties"]):
            return None, "estimates use a different county list"
        g = self._window(rec["pop"])
        return (g, None) if g is not None else (None, f"|growth| > {MAX_GROWTH:.0%}")
