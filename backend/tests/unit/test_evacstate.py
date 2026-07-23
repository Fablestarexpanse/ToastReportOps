"""Authoritative evac state: zones decide, official facts fall back, and a
reduced or cancelled zone stops showing as an order."""

import json

from toast.core.db import utcnow
from toast.pipeline.evacstate import evac_state, normalize_status

NOW = "2026-07-19T00:00:00.000Z"


def _seed_incident(db, iid="i1", name="BISCAR FIRE", lat=40.5, lon=-120.4):
    db.execute(
        "INSERT INTO incidents (id, canonical_name, status, lat, lon, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, ?, ?, ?)",
        (iid, name, lat, lon, NOW, NOW),
    )


def _zone_cache(db, features, *, fetched_at=None):
    payload = {"type": "FeatureCollection", "features": features}
    db.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        ("evaczones:ca", json.dumps(payload), fetched_at or utcnow()),
    )


def _zone(incident_id, status, zid="US-CA-XLS-LAS-543"):
    return {
        "geometry": {"type": "Point", "coordinates": [-120.4, 40.5]},
        "properties": {
            "ZONE_ID": zid,
            "ZONE_NAME": "Test Zone",
            "STATUS": status,
            "issued_at": NOW,
            "incident_id": incident_id,
        },
    }


def _evac_fact(db, iid, status, tier, content="evac", fid=None, observed=NOW):
    from toast.core.ids import ulid

    db.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES (?, ?, 'scrape', 'rss', ?, ?, ?) ON CONFLICT DO NOTHING",
        (f"s{tier}", f"src{tier}", tier, NOW, NOW),
    )
    db.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, ?, ?, 'report', '{}', ?, ?) ON CONFLICT DO NOTHING",
        (f"e{tier}", f"s{tier}", f"x{tier}", f"h{tier}", NOW),
    )
    fid = fid or ulid()
    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " evac_status, confidence, observed_at, ingested_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'evac', ?, 0.9, ?, ?)",
        (fid, iid, f"e{tier}", f"s{tier}", tier, content, status, observed, utcnow()),
    )
    return fid


def test_normalize_status_maps_cal_oes_strings():
    assert normalize_status("Evacuation Order") == "order"
    assert normalize_status("Evacuation Warning") == "warning"
    assert normalize_status("Shelter In Place") == "shelter_in_place"
    assert normalize_status("Advisory") == "advisory"
    assert normalize_status("Repopulation") == "lifted"
    assert normalize_status(None) is None


def test_strongest_zone_status_wins(db):
    _seed_incident(db)
    _zone_cache(
        db,
        [
            _zone("i1", "Evacuation Warning", "z-a"),
            _zone("i1", "Evacuation Order", "z-b"),
            _zone("i1", "Evacuation Warning", "z-c"),
        ],
    )
    st = evac_state(db, "i1")
    assert st.source == "zones"
    assert st.status == "order"  # order outranks the two warnings
    assert st.counts == {"order": 1, "warning": 2, "shelter_in_place": 0, "advisory": 0}
    assert len(st.zones) == 3


def test_reduced_zone_no_longer_shows_as_order(db):
    """The county reduced everything to a warning: the order is gone from the
    layer, so the incident must read 'warning', not 'order'."""
    _seed_incident(db)
    # even though a stale official ORDER fact is still on the ledger…
    _evac_fact(db, "i1", "order", tier=2)
    _zone_cache(db, [_zone("i1", "Evacuation Warning", "z-a")])
    st = evac_state(db, "i1")
    assert st.status == "warning"


def test_cancelled_all_zones_gone_reads_clear(db):
    """No zones left for the fire and no official fact → nothing in effect,
    regardless of stale media 'order' facts."""
    _seed_incident(db)
    _evac_fact(db, "i1", "order", tier=4)  # media only
    _zone_cache(db, [_zone("other-fire", "Evacuation Order")])  # not our incident
    st = evac_state(db, "i1")
    assert st.status is None
    assert st.source == "none"  # media never paints the pin


def test_official_fact_fallback_when_no_zone(db):
    _seed_incident(db)
    _evac_fact(db, "i1", "order", tier=2)
    _zone_cache(db, [])  # zone layer present but empty
    st = evac_state(db, "i1")
    assert st.status == "order"
    assert st.source == "facts"


def test_stale_zone_cache_is_not_trusted(db):
    """A zone snapshot older than the trust window must not keep asserting an
    active order — fall back to official facts instead."""
    _seed_incident(db)
    _zone_cache(
        db,
        [_zone("i1", "Evacuation Order")],
        fetched_at="2020-01-01T00:00:00.000Z",
    )
    st = evac_state(db, "i1")
    assert st.source in ("facts", "none")  # stale zones ignored
    assert st.status is None
