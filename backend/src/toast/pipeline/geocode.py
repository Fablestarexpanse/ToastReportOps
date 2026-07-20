"""Place-name geocoding from USGS GNIS.

Two jobs:

1. Turn location text ("near Loyalton", "Arrowhead Mine Road, Nevada County")
   into coordinates, so the attribution scorer's geo term stops being dead
   weight.
2. Resolve a FIRE NAME to a place. California wildfires are named after nearby
   features — Biscar, Elephant, Pit, Dove — so a fire nobody official has
   listed yet can still be put on the map.

Discipline: a geocode is weaker evidence than EXIF or an official perimeter,
and an ambiguous name is worse than no answer. When several equally-good
candidates disagree, this module returns nothing rather than guessing — a wrong
pin on a fire map is a real-world hazard.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

GNIS_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/"
    "DomesticNames/DomesticNames_CA_Text.zip"
)

# Feature classes worth matching, roughly in order of how usefully they locate
# a fire. Administrative/ephemeral classes (Church, School, Bridge…) are noisy
# and are excluded entirely.
CLASS_PRIORITY = {
    "Populated Place": 10,
    "Locale": 6,
    "Summit": 9,
    "Range": 8,
    "Ridge": 8,
    "Valley": 8,
    "Canyon": 8,
    "Flat": 7,
    "Basin": 7,
    "Lake": 7,
    "Reservoir": 7,
    "Stream": 6,
    "Spring": 5,
    "Park": 6,
    "Forest": 7,
    "Island": 6,
    "Cape": 5,
    "Bay": 5,
    "Mine": 4,
    "Well": 3,
    "Trail": 4,
    "Area": 5,
}

# Words that never identify a place on their own.
STOP_WORDS = {
    "fire",
    "complex",
    "incident",
    "county",
    "california",
    "the",
    "near",
    "north",
    "south",
    "east",
    "west",
    "upper",
    "lower",
    "old",
    "new",
}

MAX_CONFIDENCE = 0.7  # a place-name match must never alone auto-attribute


@dataclass(frozen=True)
class GeoMatch:
    lat: float
    lon: float
    name: str
    feature_class: str
    county: str | None
    confidence: float
    method: str = "geocoded_text"


def norm(text: str) -> str:
    text = re.sub(r"\(.*?\)", " ", text or "").lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_qualifiers(name: str) -> str:
    """'Dove Fire' → 'dove'; 'near Loyalton' → 'loyalton'."""
    words = [w for w in norm(name).split() if w not in STOP_WORDS]
    return " ".join(words)


def gnis_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM gnis_features").fetchone()["n"])
    except sqlite3.OperationalError:
        return 0


def _pick(
    candidates: list[sqlite3.Row], county: str | None, base_confidence: float
) -> GeoMatch | None:
    """Choose among same-named features, or refuse when they disagree."""
    county_norm = norm(county or "").replace(" county", "").strip()
    if county_norm:
        in_county = [r for r in candidates if norm(r["county"] or "") == county_norm]
        if not in_county:
            # a county was claimed and nothing there matches — do not fall back
            # to a same-named feature elsewhere in the state
            return None
        best = max(in_county, key=lambda r: CLASS_PRIORITY[r["feature_class"]])
        confidence = base_confidence
    else:
        # No county hint: only answer when the state-wide match is unambiguous.
        top = max(CLASS_PRIORITY[r["feature_class"]] for r in candidates)
        tied = [r for r in candidates if CLASS_PRIORITY[r["feature_class"]] == top]
        if len({norm(r["county"] or "") for r in tied}) > 1:
            return None  # several counties claim this name — refuse to guess
        best = tied[0]
        confidence = round(base_confidence * 0.8, 2)  # nobody told us the county

    return GeoMatch(
        lat=best["lat"],
        lon=best["lon"],
        name=best["name"],
        feature_class=best["feature_class"],
        county=best["county"],
        confidence=confidence,
    )


def _lookup(conn: sqlite3.Connection, key: str, *, prefix: bool) -> list[sqlite3.Row]:
    sql = "SELECT name, feature_class, county, lat, lon FROM gnis_features WHERE name_norm "
    sql += "LIKE ?" if prefix else "= ?"
    rows = conn.execute(sql, (f"{key} %" if prefix else key,)).fetchall()
    return [
        r
        for r in rows
        # GNIS keeps vanished settlements ("Dove (historical)"); a place that no
        # longer exists is a bad guess at where a fire is burning today
        if r["feature_class"] in CLASS_PRIORITY and "(historical)" not in r["name"].lower()
    ]


def resolve_place(
    conn: sqlite3.Connection, text: str, *, county: str | None = None
) -> GeoMatch | None:
    """Best coordinates for a place name, or None when it cannot be pinned."""
    key = _strip_qualifiers(text)
    if not key or len(key) < 3:
        return None
    candidates = _lookup(conn, key, prefix=False)
    if not candidates:
        return None
    return _pick(candidates, county, MAX_CONFIDENCE)


def resolve_fire_name(
    conn: sqlite3.Connection, fire_name: str, *, county: str | None = None
) -> GeoMatch | None:
    """Locate a fire by the feature it is named after.

    California fires take the distinctive word of a nearby feature, so 'Dove
    Fire' is named for Dove Creek and 'Biscar Fire' for Biscar. Try the exact
    name first, then that word as the start of a feature name.

    A COUNTY IS REQUIRED. Fire names are ordinary words — there are Thorns,
    Springs and Mills all over California — and without a county the "unique"
    match is frequently the wrong end of the state. Reporting virtually always
    names a county, so demanding one costs little and prevents putting a fire
    400 miles from where it is burning."""
    key = _strip_qualifiers(fire_name)
    if not key or len(key) < 3 or not county:
        return None

    candidates = _lookup(conn, key, prefix=False) or _lookup(conn, key, prefix=True)
    if not candidates:
        return None
    # a naming convention is strong evidence, not proof — discount it
    match = _pick(candidates, county, round(MAX_CONFIDENCE * 0.85, 2))
    if match is None:
        return None
    return GeoMatch(
        lat=match.lat,
        lon=match.lon,
        name=match.name,
        feature_class=match.feature_class,
        county=match.county,
        confidence=match.confidence,
        method="named_feature",
    )


def load_gnis(conn: sqlite3.Connection, *, data: bytes | None = None) -> dict[str, int]:
    """Load (or reload) the California gazetteer. Idempotent."""
    import io
    import zipfile

    from toast.core.fetch import fetch
    from toast.core.logging import get_logger

    if data is None:
        data, _ = fetch(GNIS_URL, accept_types=(), max_bytes=40 * 1024 * 1024)

    archive = zipfile.ZipFile(io.BytesIO(data))
    member = next(n for n in archive.namelist() if n.lower().endswith(".txt"))

    rows: list[tuple] = []
    skipped = 0
    with archive.open(member) as fh:
        header = fh.readline().decode("utf-8-sig").rstrip("\r\n").split("|")
        idx = {name: i for i, name in enumerate(header)}
        for raw in fh:
            parts = raw.decode("utf-8", "replace").rstrip("\r\n").split("|")
            if len(parts) <= idx["prim_long_dec"]:
                skipped += 1
                continue
            try:
                lat = float(parts[idx["prim_lat_dec"]])
                lon = float(parts[idx["prim_long_dec"]])
                feature_id = int(parts[idx["feature_id"]])
            except (ValueError, KeyError):
                skipped += 1
                continue
            if not (lat and lon):
                skipped += 1
                continue
            name = parts[idx["feature_name"]]
            rows.append(
                (
                    feature_id,
                    name,
                    norm(name),
                    parts[idx["feature_class"]],
                    parts[idx["state_name"]],
                    parts[idx["county_name"]] or None,
                    lat,
                    lon,
                )
            )

    conn.execute("DELETE FROM gnis_features")
    conn.executemany(
        """INSERT OR REPLACE INTO gnis_features
           (feature_id, name, name_norm, feature_class, state, county, lat, lon)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    get_logger("gnis").info("gnis_loaded", features=len(rows), skipped=skipped)
    return {"loaded": len(rows), "skipped": skipped}
