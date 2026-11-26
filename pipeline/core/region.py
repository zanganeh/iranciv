"""The country this map draws, in one place.

Everything that was "the lower 48" in usaciv is read from here, so the builders do not hard-code a country.
"""

COUNTRY = "Iran"
COUNTRY_ISO3 = "IRN"
CAPITAL = "Tehran"

# Albers equal-area conic fitted to Iran (about 25-40 N, 44-63.5 E): standard parallels at 1/6 and 5/6 of the span.
ALBERS = {"lat1": 27.5, "lat2": 37.5, "lat0": 32.5, "lon0": 53.5}

# lon/lat window around the country and the map margin (neighbours, the Caspian, the Gulf).
BBOX = (40.5, 22.0, 66.5, 42.5)

HEX_RADIUS_KM = 15.0
MARGIN_KM = 160.0
