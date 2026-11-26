"""The game-concept → real-data mapping for Iran, in one place.

Each yield is defined once here and exported to the browser (nation.json), so the UI and the Almanac describe exactly
what was computed. Iran publishes no county-level jobs-by-industry table that is reachable from here, so unlike
usaciv the non-Gold yields are not worker counts: each is a real, mapped quantity, named in its description.
"""

from __future__ import annotations

HEX_AREA_KM2 = 3 * 3 ** 0.5 / 2 * 15.0 ** 2  # pointy-top hex of radius 15 km

YIELDS = [
    {
        "key": "food",
        "source": "ESA WorldCover 2021",
        "icon": "y-food",
        "emoji": "🌾",
        "label": "Food",
        "color": "#7fbf3f",
        "unit": "km²",
        "industries": [],
        "description": "Cropland area in square kilometres: the cropland share of each hex in ESA WorldCover 2021 "
        "(10 m satellite land cover) times the hex's area, summed over the area's hexes.",
    },
    {
        "key": "production",
        "source": "WRI Global Power Plant Database v1.3 (2021)",
        "icon": "y-production",
        "emoji": "⚙️",
        "label": "Production",
        "color": "#d9823b",
        "unit": "MW",
        "industries": [],
        "description": "Power-plant capacity in megawatts located in the area (WRI Global Power Plant Database, last "
        "updated 2021). A stand-in for industrial capacity; nuclear plants are left out of this map.",
    },
    {
        "key": "gold",
        "icon": "y-gold",
        "emoji": "💰",
        "label": "Gold",
        "color": "#f2c94c",
        "unit": "usd",
        "industries": [],
        "description": "Gross domestic product per year in current US dollars, 2016. Each province's share of GDP in "
        "the Statistical Centre of Iran's provincial accounts (1395, 2016/17) times the World Bank's national GDP "
        "for 2016. County and city GDP is an estimate: the province's GDP split by population share.",
    },
    {
        "key": "science",
        "source": "Wikidata, queried 2026-11-26",
        "icon": "y-science",
        "emoji": "🔬",
        "label": "Science",
        "color": "#4fa3e0",
        "unit": "universities",
        "industries": [],
        "description": "Universities with a Wikipedia article in at least 8 languages (Wikidata), counted where their "
        "main campus is. A rough signal of research prominence, not a measure of output.",
    },
    {
        "key": "culture",
        "source": "UNESCO World Heritage List via Wikidata, queried 2026-11-26",
        "icon": "y-culture",
        "emoji": "🎭",
        "label": "Culture",
        "color": "#b07ce0",
        "unit": "sites",
        "industries": [],
        "description": "UNESCO World Heritage properties (Wikidata), counted where they are. Parts of one property "
        "inscribed the same year and within 40 km of each other count once.",
    },
]
YIELD_KEYS = [y["key"] for y in YIELDS]
