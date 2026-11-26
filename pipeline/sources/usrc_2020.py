"""2020 U.S. Religion Census, county file with religious-tradition totals (ARDA "RCMSCY20", hosted on OSF).

Keeps, per county: the 2020 population the census used, total adherents, adherents of the six ARDA
religious traditions (RELTRAD), and a few bodies split out of them for the map (Latter-day Saints,
which ARDA files under "Other"; the Jewish movements; Muslims; non-denominational churches).

Terms: the ARDA usage agreement asks users to credit ARDA and the original collectors and to use the
data responsibly; it does not forbid publishing results. The ASARB workbook is "all rights reserved",
so the app ships only derived county figures (rates and the largest tradition), never the raw file.
"""

from __future__ import annotations

import zipfile

from ._resource_util import num, xlsx_records
from ..core.source import Source, SourceInfo

URL = "https://osf.io/download/5a6sk/"  # 302 → files.osf.io; the ARDA page's "Continue" click-through links here
FILE = "RCMSCY20.xlsx"

# Interim key → ARDA adherent columns summed (empty cells mean "none reported" for a body or tradition).
TRADITIONS = {
    "evangelical": ["EVANADH_2020"],
    "black_protestant": ["BPRTADH_2020"],
    "mainline": ["MPRTADH_2020"],
    "catholic": ["CATHADH_2020"],
    "orthodox": ["ORTHADH_2020"],
    "other": ["OTHADH_2020"],  # includes Latter-day Saints, Jewish, Muslim, Buddhist, Hindu… (split out below)
}
BODIES = {
    "lds": ["LDSADH_2020"],  # The Church of Jesus Christ of Latter-day Saints (part of ARDA "Other")
    "jewish": ["CJUDADH_2020", "IJUDADH_2020", "OJUDADH_2020", "RJUDADH_2020", "RFRMADH_2020"],
    "muslim": ["MSLMADH_2020"],
    "nondenominational": ["NONDADH_2020"],  # part of ARDA "Evangelical Protestant"
}


class UsReligionCensus2020(Source):
    info = SourceInfo(
        id="usrc_2020",
        title="2020 U.S. Religion Census: county file (RCMSCY20)",
        publisher="Association of Statisticians of American Religious Bodies (ASARB); distributed by the "
        "Association of Religion Data Archives (ARDA)",
        homepage="https://www.thearda.com/data-archive?fid=RCMSCY20",
        license="Copyright 2022 ASARB. Used under the ARDA usage agreement, which asks for credit and responsible "
        "use; only derived county figures are published here. Credit: the data were downloaded from the "
        "Association of Religion Data Archives, www.theARDA.com, and were collected by the Association of "
        "Statisticians of American Religious Bodies (Grammich et al. 2023).",
        description="Congregations and their adherents by county in 2020, as reported by the participating religious bodies. "
        "Adherents are counted by congregations (members, their children and regular attenders), where the "
        "congregation meets, not by asking residents what they believe; about half of Americans are counted, "
        "some groups (notably many historically Black denominations) are under-reported, and a county with a "
        "big church can show more adherents than residents. Adherents per 100 residents are computed here as "
        "adherents ÷ the census's 2020 population × 100 (the file's own rate columns are not used). "
        "Traditions follow ARDA's grouping, with Latter-day Saints split out of 'Other'. Connecticut is not "
        "shown: this file uses its 8 old counties, which do not nest in the planning regions the map uses.",
        used_for=["Faith lens", "Dominant faith tradition lens", "County Explorer faith columns"],
    )

    def fetch(self, force=False):
        path = self.download(URL, FILE, force=force, expect_binary=True)
        if not zipfile.is_zipfile(path):
            self.discard(FILE)
            raise RuntimeError(f"{URL} did not return an xlsx file (ARDA/OSF may have moved it)")

    def process(self):
        counties = {}
        for rec in xlsx_records(self.raw_dir / FILE, "TOTADH_2020"):
            fips = (rec.get("FIPS") or "").strip()
            if not fips.isdigit():
                continue
            fips = fips.zfill(5)
            pop = num(rec.get("POP2020"))
            total = num(rec.get("TOTADH_2020"))

            def adh(cols):
                return sum(num(rec.get(c)) or 0 for c in cols)

            counties[fips] = {
                "name": rec.get("COUNAM") or None,
                "pop": pop,
                "adherents": total,  # None when the cell is blank: no data, not zero
                "congregations": num(rec.get("TOTCNG_2020")),
                **{k: (adh(cols) if total is not None else None) for k, cols in {**TRADITIONS, **BODIES}.items()},
            }
        if len(counties) < 3000:
            raise RuntimeError(f"{FILE}: only {len(counties)} county rows (expected ~3,140)")
        la = counties.get("06037")
        if not la or not la["adherents"] or not 0.3 < la["adherents"] / la["pop"] < 0.8:
            raise RuntimeError(f"{FILE}: Los Angeles County (06037) adherents look wrong: {la}")
        return {
            "vintage": "2020 U.S. Religion Census",
            "year": 2020,
            "traditions": list(TRADITIONS),
            "bodies": list(BODIES),
            "counties": counties,
            "_files": [FILE],
        }
