"""GNIS geocoding — the important behaviour is REFUSING to guess."""

import io
import zipfile

from toast.pipeline.geocode import (
    gnis_count,
    load_gnis,
    norm,
    resolve_fire_name,
    resolve_place,
)

HEADER = (
    "feature_id|feature_name|feature_class|state_name|state_numeric|county_name|"
    "county_numeric|map_name|date_created|date_edited|bgn_type|bgn_authority|bgn_date|"
    "prim_lat_dms|prim_long_dms|prim_lat_dec|prim_long_dec|source_lat_dms|"
    "source_long_dms|source_lat_dec|source_long_dec"
)


def _row(fid, name, cls, county, lat, lon):
    cols = [""] * 21
    cols[0], cols[1], cols[2], cols[3], cols[5] = str(fid), name, cls, "California", county
    cols[15], cols[16] = str(lat), str(lon)
    return "|".join(cols)


FIXTURE = [
    _row(1, "Dove Creek", "Stream", "Butte", 39.75, -121.60),
    _row(2, "Loyalton", "Populated Place", "Sierra", 39.676, -120.242),
    _row(3, "Biscar", "Locale", "Lassen", 40.55, -120.35),
    # the same name in two counties — must refuse without a county hint
    _row(4, "Mill Creek", "Stream", "Tehama", 40.10, -121.50),
    _row(5, "Mill Creek", "Stream", "Shasta", 40.90, -122.10),
    # noisy class that must never be matched
    _row(6, "Paradise Elementary School", "School", "Butte", 39.75, -121.62),
    _row(7, "Paradise", "Populated Place", "Butte", 39.7596, -121.6219),
    # a settlement that no longer exists — must not be used as a location
    _row(8, "Thorn (historical)", "Populated Place", "Humboldt", 40.06, -123.96),
]


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Text/DomesticNames_CA.txt", "﻿" + HEADER + "\n" + "\n".join(FIXTURE) + "\n")
    return buf.getvalue()


def _loaded(db):
    load_gnis(db, data=_zip_bytes())
    return db


def test_load_is_idempotent(db):
    load_gnis(db, data=_zip_bytes())
    first = gnis_count(db)
    load_gnis(db, data=_zip_bytes())
    assert gnis_count(db) == first == len(FIXTURE)


def test_resolves_a_populated_place(db):
    match = resolve_place(_loaded(db), "Loyalton")
    assert match is not None
    assert (round(match.lat, 3), round(match.lon, 3)) == (39.676, -120.242)
    assert match.county == "Sierra"
    assert match.method == "geocoded_text"


def test_strips_qualifiers_from_location_text(db):
    assert resolve_place(_loaded(db), "near Loyalton") is not None
    assert resolve_place(_loaded(db), "Loyalton, California") is not None


def test_ambiguous_name_returns_nothing(db):
    """Mill Creek exists in two counties — a wrong pin is worse than no pin."""
    assert resolve_place(_loaded(db), "Mill Creek") is None


def test_county_hint_disambiguates(db):
    match = resolve_place(_loaded(db), "Mill Creek", county="Tehama")
    assert match is not None and match.county == "Tehama"
    match = resolve_place(_loaded(db), "Mill Creek", county="Shasta County")
    assert match is not None and match.county == "Shasta"


def test_wrong_county_does_not_fall_back_elsewhere(db):
    """A claim naming a county we can't match must not borrow another one."""
    assert resolve_place(_loaded(db), "Loyalton", county="San Diego") is None


def test_noisy_feature_classes_are_ignored(db):
    assert resolve_place(_loaded(db), "Paradise Elementary School") is None


def test_vanished_settlements_are_ignored(db):
    """GNIS keeps historical places; where a ghost town stood in 1880 is a bad
    guess at where a fire is burning today."""
    assert resolve_place(_loaded(db), "Thorn") is None
    assert resolve_fire_name(_loaded(db), "Thorn Fire", county="Humboldt") is None


def test_fire_name_without_a_county_is_refused(db):
    """Fire names are ordinary words — Thorn, Spring, Mill exist statewide.
    Without a county the 'unique' match is often the wrong end of California."""
    assert resolve_fire_name(_loaded(db), "Dove Fire") is None
    assert resolve_fire_name(_loaded(db), "Dove Fire", county="Butte") is not None


def test_unknown_place_returns_nothing(db):
    assert resolve_place(_loaded(db), "Nowheresville") is None
    assert resolve_place(_loaded(db), "") is None


def test_fire_name_resolves_to_its_namesake_feature(db):
    """'Dove Fire' → Dove Creek: how a brand-new fire gets on the map."""
    match = resolve_fire_name(_loaded(db), "Dove Fire", county="Butte")
    assert match is not None
    assert match.name == "Dove Creek"
    assert match.method == "named_feature"


def test_fire_name_confidence_is_capped_below_auto_attribution(db):
    from toast.pipeline.attribution import AUTO_THRESHOLD

    match = resolve_fire_name(_loaded(db), "Biscar Fire", county="Lassen")
    assert match is not None
    # a naming convention is evidence, not proof — it must never alone
    # push a fact over the auto-attribute line
    assert match.confidence < AUTO_THRESHOLD


def test_norm_handles_punctuation_and_parentheses():
    assert norm("Olive City (historical)") == "olive city"
    assert norm("St. Helena's Ridge") == "st helena s ridge"


def test_geocoding_missing_table_is_not_fatal(db):
    """A workstation that has never loaded GNIS keeps working."""
    db.execute("DROP TABLE gnis_features")
    assert gnis_count(db) == 0
