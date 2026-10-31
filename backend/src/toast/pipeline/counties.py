"""California county centroids — the last-resort answer to "where is it?".

Approximate geographic centres of the 58 counties (public-domain figures,
rounded to ~1 km). County-level is the honest floor for a fire that reporting
has named but nobody has pinned: a real place on the map, clearly labelled as
no more precise than the county itself.
"""

from __future__ import annotations

CA_COUNTY_CENTROIDS: dict[str, tuple[float, float]] = {
    "alameda": (37.65, -121.92),
    "alpine": (38.60, -119.82),
    "amador": (38.45, -120.65),
    "butte": (39.67, -121.60),
    "calaveras": (38.20, -120.55),
    "colusa": (39.18, -122.24),
    "contra costa": (37.92, -121.95),
    "del norte": (41.74, -123.90),
    "el dorado": (38.78, -120.52),
    "fresno": (36.76, -119.65),
    "glenn": (39.60, -122.39),
    "humboldt": (40.70, -123.92),
    "imperial": (33.04, -115.36),
    "inyo": (36.51, -117.41),
    "kern": (35.34, -118.73),
    "kings": (36.08, -119.82),
    "lake": (39.10, -122.75),
    "lassen": (40.67, -120.59),
    "los angeles": (34.20, -118.23),
    "madera": (37.22, -119.76),
    "marin": (38.07, -122.74),
    "mariposa": (37.58, -119.91),
    "mendocino": (39.44, -123.39),
    "merced": (37.19, -120.72),
    "modoc": (41.59, -120.72),
    "mono": (37.94, -118.89),
    "monterey": (36.22, -121.24),
    "napa": (38.51, -122.33),
    "nevada": (39.30, -120.77),
    "orange": (33.70, -117.76),
    "placer": (39.06, -120.72),
    "plumas": (40.00, -120.84),
    "riverside": (33.74, -115.99),
    "sacramento": (38.45, -121.34),
    "san benito": (36.61, -121.09),
    "san bernardino": (34.84, -116.18),
    "san diego": (33.03, -116.74),
    "san francisco": (37.76, -122.44),
    "san joaquin": (37.93, -121.27),
    "san luis obispo": (35.39, -120.45),
    "san mateo": (37.42, -122.33),
    "santa barbara": (34.67, -120.02),
    "santa clara": (37.23, -121.70),
    "santa cruz": (37.03, -122.01),
    "shasta": (40.76, -122.04),
    "sierra": (39.58, -120.52),
    "siskiyou": (41.59, -122.54),
    "solano": (38.27, -121.94),
    "sonoma": (38.53, -122.89),
    "stanislaus": (37.56, -120.99),
    "sutter": (39.03, -121.69),
    "tehama": (40.13, -122.24),
    "trinity": (40.65, -123.11),
    "tulare": (36.22, -118.80),
    "tuolumne": (38.03, -119.95),
    "ventura": (34.36, -119.13),
    "yolo": (38.69, -121.90),
    "yuba": (39.27, -121.35),
}


def county_centroid(county_text: str | None) -> tuple[str, float, float] | None:
    """Match free-text county mentions ('Butte County, California') to a
    centroid. Returns (canonical name, lat, lon) or None."""
    if not county_text:
        return None
    low = county_text.lower()
    for name, (lat, lon) in CA_COUNTY_CENTROIDS.items():
        if name in low:
            return name.title(), lat, lon
    return None
