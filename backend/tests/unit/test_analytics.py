"""ANALYTICS — every number must come from stored rows, with honest denominators."""

from toast.core.ids import ulid
from toast.pipeline.analytics import overview, records

NOW = "2026-07-19T12:00:00.000Z"


def _fact(db, *, incident_id="i1", kind="acreage", tier=1, acreage=None, supersedes=None):
    fid = ulid()
    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " acreage, confidence, observed_at, ingested_at, supersedes)"
        " VALUES (?, ?, 'e1', 's1', ?, 'c', ?, ?, 0.8, ?, ?, ?)",
        (fid, incident_id, tier, kind, acreage, NOW, NOW, supersedes),
    )
    return fid


def test_empty_ledger_reports_no_rates_rather_than_zero(db):
    """A rate over nothing is not 0% — it is unknown, and must say so."""
    data = overview(db)
    assert data["totals"]["facts"] == 0
    assert data["quality"]["attribution_rate"] is None
    assert data["quality"]["corroboration_rate"] is None


def test_longest_burning_counts_from_the_fire_not_from_when_we_first_saw_it(seeded):
    """A fresh install tracking a two-week-old fire called it day 1."""
    _fact(seeded, kind="start")
    seeded.execute(
        "UPDATE incidents SET status = 'active', created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE id = 'i1'"
    )
    longest = records(seeded)["longest_burning"]
    assert longest["name"]
    assert longest["days"] >= 30  # NOW is in 2026-07, long before today


def test_totals_and_breakdowns_come_from_rows(seeded):
    _fact(seeded, kind="acreage", acreage=100)
    _fact(seeded, kind="evac", tier=2)
    _fact(seeded, kind="evac", tier=4)
    data = overview(seeded)

    assert data["totals"]["facts"] == 3
    assert data["totals"]["incidents"] == 1
    kinds = {k["kind"]: k["n"] for k in data["by_kind"]}
    assert kinds == {"acreage": 1, "evac": 2}
    tiers = {t["tier"]: t["n"] for t in data["by_tier"]}
    assert tiers == {1: 1, 2: 1, 4: 1}


def test_attribution_rate_excludes_triaged_facts(seeded):
    """Triaged facts left the queue by decision; counting them as failures
    would permanently understate attribution."""
    _fact(seeded, incident_id="i1")
    orphan = _fact(seeded, incident_id=None, kind="start")
    assert overview(seeded)["quality"]["attribution_rate"] == 0.5

    seeded.execute(
        "INSERT INTO fact_triage (fact_id, state, at) VALUES (?, 'not_incident', ?)",
        (orphan, NOW),
    )
    data = overview(seeded)
    assert data["quality"]["attribution_rate"] == 1.0
    assert data["quality"]["triaged"] == 1


def test_corroboration_rate_carries_its_sample_size(seeded):
    f1 = _fact(seeded)
    f2 = _fact(seeded)
    for fid, n in ((f1, 2), (f2, 0)):
        seeded.execute(
            "INSERT INTO fact_assessments (fact_id, corroborating_sources,"
            " effective_confidence, updated_at) VALUES (?, ?, 0.7, ?)",
            (fid, n, NOW),
        )
    q = overview(seeded)["quality"]
    assert q["assessed"] == 2 and q["corroborated"] == 1
    assert q["corroboration_rate"] == 0.5


def test_acres_uses_latest_non_superseded_value(seeded):
    old = _fact(seeded, kind="acreage", acreage=100)
    _fact(seeded, kind="acreage", acreage=5000, supersedes=old)
    # only the current value counts, never the sum of the chain
    assert overview(seeded)["totals"]["acres_tracked"] == 5000


def test_daily_series_is_bounded_by_the_window(seeded):
    _fact(seeded)
    assert len(overview(seeded, days=14)["daily"]) <= 15
