"""Incident history: the growth curve and the perimeter archive.

Supersession IS the history — every reported acreage in time order is the
growth curve. And perimeter snapshots must be idempotent: a hundred re-fetches
store nothing until the IR flight actually flies again.
"""

from toast.api import routes
from toast.core.ids import ulid
from toast.pipeline.perimeters import _archive_snapshots


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def _fact(db, kind, value, observed_at, col="acreage"):
    db.execute(
        f"INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        f" {col}, structured, confidence, observed_at, ingested_at)"
        f" VALUES (?, 'i1', 'e1', 's1', 2, 'x', ?, ?, '{{}}', 0.9, ?, ?)",
        (ulid(), kind, value, observed_at, observed_at),
    )


def test_series_is_time_ordered_with_repeats_collapsed(seeded):
    _fact(seeded, "acreage", 100, "2026-07-18T01:00:00.000Z")
    _fact(seeded, "acreage", 450, "2026-07-18T09:00:00.000Z")
    _fact(seeded, "acreage", 450, "2026-07-18T12:00:00.000Z")  # same number, later
    _fact(seeded, "acreage", 1200, "2026-07-19T01:00:00.000Z")
    _fact(seeded, "containment", 10, "2026-07-18T09:00:00.000Z", col="containment_pct")
    out = routes.incident_history(Req(seeded), "i1")
    assert [p["value"] for p in out["acreage"]] == [100, 450, 1200]
    assert out["containment"][0]["value"] == 10


def _feature(iid, mapped_ms, acres=500.0):
    return {
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "properties": {
            "incident_id": iid,
            "poly_DateCurrent": mapped_ms,
            "poly_GISAcres": acres,
        },
    }


def test_snapshots_store_once_per_mapping(seeded):
    feats = [_feature("i1", 1_752_900_000_000)]
    assert _archive_snapshots(seeded, feats) == 1
    assert _archive_snapshots(seeded, feats) == 0  # re-fetch, same flight: no new row

    # the next IR flight (new mapped time) IS a new snapshot
    assert _archive_snapshots(seeded, [_feature("i1", 1_752_990_000_000, 900.0)]) == 1
    out = routes.incident_history(Req(seeded), "i1")
    assert out["perimeter_count"] == 2
    assert out["perimeters"][0]["acres"] == 500.0
    assert "geometry" not in out["perimeters"][0]  # shapes only on request

    shaped = routes.incident_history(Req(seeded), "i1", shapes=True)
    assert shaped["perimeters"][0]["geometry"]["type"] == "Polygon"


def test_unmatched_feature_is_not_archived(seeded):
    assert _archive_snapshots(seeded, [_feature(None, 1_752_900_000_000)]) == 0
