"""CDC PLACES: county data (GIS-friendly wide format), model-based estimates of adult health measures.

Uses the Socrata SODA /resource/ endpoint (the old rows.csv?accessType=DOWNLOAD export was deprecated in
2026). Keeps the age-adjusted prevalence (`<measure>_adjprev`, percent of adults) of the measures the
Wellbeing lens (id amenities) and the Explorer use. Blank cells stay null: PLACES leaves some measures out for whole
states (e.g. the BRFSS core measures for Kentucky and Pennsylvania in the 2025 release).
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from ..core.source import Source, SourceInfo

DATASET = "i46a-9kgh"
URL = f"https://data.cdc.gov/resource/{DATASET}.csv?$limit=5000"  # default page is 1,000 rows: always pass $limit
META_URL = f"https://data.cdc.gov/api/views/{DATASET}.json"
FILE, META = "places_county_gis.csv", "places_county_gis_meta.json"

MEASURES = {  # PLACES short name → label (all are % of adults 18+)
    "mhlth": "Frequent mental distress (14+ bad mental-health days in the past 30)",
    "depression": "Ever told they have a depressive disorder",
    "ghlth": "Fair or poor self-rated health",
    "lpa": "No leisure-time physical activity",
    "phlth": "Frequent physical distress",
    "loneliness": "Feeling lonely always, usually or sometimes",
    "emotionspt": "Lacking social and emotional support",
    "sleep": "Short sleep (under 7 hours)",
    "binge": "Binge drinking",
}


class CdcPlaces(Source):
    info = SourceInfo(
        id="cdc_places",
        title="PLACES: Local Data for Better Health, county data",
        publisher="Centers for Disease Control and Prevention (CDC), Division of Population Health",
        homepage="https://www.cdc.gov/places/",
        license="Public domain (CDC; Socrata licence PUBLIC_DOMAIN)",
        description="Model-based county estimates of adult health measures from the Behavioral Risk Factor "
        "Surveillance System (mostly 2023 survey data in the 2025 release). They are small-area statistical "
        "estimates, not direct counts or a happiness survey; small counties are pulled toward their state and "
        "demographic averages. Age-adjusted prevalences are used so places with older or younger populations "
        "compare fairly. The 2025 release has no core measures for Kentucky or Pennsylvania, and no "
        "social-needs measures (e.g. loneliness) for several states; those stay blank, never imputed.",
        used_for=["Wellbeing lens (id amenities)", "County Explorer health columns"],
    )

    def fetch(self, force=False):
        path = self.download(URL, FILE, force=force)
        with open(path, encoding="utf-8") as f:
            head = f.readline()
        if "countyfips" not in head:
            self.discard(FILE)
            raise RuntimeError(f"{URL} did not return the PLACES county CSV (Socrata API may have changed)")
        self.download(META_URL, META, force=force)

    def process(self):
        text = (self.raw_dir / FILE).read_text(encoding="utf-8")
        counties = {}
        for row in csv.DictReader(io.StringIO(text)):
            fips = (row.get("countyfips") or "").strip()
            if not (len(fips) == 5 and fips.isdigit()):
                continue

            def val(m):
                v = (row.get(f"{m}_adjprev") or "").strip()
                try:
                    return float(v)
                except ValueError:
                    return None

            counties[fips] = {"state": row.get("stateabbr"), **{m: val(m) for m in MEASURES}}
        if len(counties) < 3000:
            raise RuntimeError(f"{FILE}: only {len(counties)} counties (expected ~3,140; was $limit honoured?)")
        release, updated = None, None
        meta_path = self.raw_dir / META
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            ts = meta.get("rowsUpdatedAt")
            updated = datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if isinstance(ts, (int, float)) else None
            name = meta.get("name") or ""
            release = next((w for w in name.split() if w.isdigit() and len(w) == 4), None)
        files = [FILE] + ([META] if meta_path.exists() else [])
        return {
            "vintage": f"CDC PLACES{' ' + release if release else ''} release",
            "release": release,
            "rows_updated_at": updated,
            "unit": "percent of adults, age-adjusted",
            "measures": MEASURES,
            "counties": counties,
            "_files": files,
        }
