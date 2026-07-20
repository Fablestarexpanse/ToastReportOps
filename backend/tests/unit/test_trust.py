import sqlite3

import pytest

from toast.core.ids import ulid
from toast.pipeline.trust import (
    MIN_FACTS_TO_JUDGE,
    _suggest_tier,
    apply_tier,
    compute_source_stats,
    dismiss_suggestion,
)

NOW = "2026-07-19T00:00:00.000Z"


def _source(db: sqlite3.Connection, sid: str, tier: int, name: str | None = None) -> str:
    db.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES (?, ?, 'scrape', 'rss', ?, ?, ?)",
        (sid, name or sid, tier, NOW, NOW),
    )
    return sid


def _fact(
    db: sqlite3.Connection,
    source_id: str,
    tier: int,
    *,
    incident_id: str | None = "i1",
    kind: str = "acreage",
    observed_at: str = "2026-07-19T12:00:00.000Z",
    ingested_at: str | None = None,
    supersedes: str | None = None,
) -> str:
    fid = ulid()
    db.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, ?, ?, 'article', '{}', ?, ?)",
        (f"e-{fid}", source_id, fid, f"h-{fid}", NOW),
    )
    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at, supersedes)"
        " VALUES (?, ?, ?, ?, ?, 'claim', ?, 0.8, ?, ?, ?)",
        (
            fid,
            incident_id,
            f"e-{fid}",
            source_id,
            tier,
            kind,
            observed_at,
            ingested_at or observed_at,  # default: learned when it happened
            supersedes,
        ),
    )
    return fid


def _recent(hours_ago: int) -> str:
    """An observed_at inside the 30-day window, relative to the test DB clock."""
    import datetime as dt

    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


# ---- suggestion rules (pure) ----


def test_no_suggestion_without_enough_evidence():
    assert (
        _suggest_tier(
            tier=4, fact_count=MIN_FACTS_TO_JUDGE - 1, correction_rate=0.0, lead_median_s=9999
        )
        is None
    )


def test_fast_and_accurate_source_is_promoted():
    assert _suggest_tier(tier=4, fact_count=20, correction_rate=0.0, lead_median_s=3600) == 3


def test_frequently_corrected_source_is_demoted():
    assert _suggest_tier(tier=3, fact_count=20, correction_rate=0.5, lead_median_s=9999) == 4


def test_accurate_but_slow_source_stays_put():
    assert _suggest_tier(tier=4, fact_count=20, correction_rate=0.0, lead_median_s=60) is None


def test_never_promotes_into_the_official_tier():
    # T2 is as high as measurement can take a source; T1 is editorial.
    assert _suggest_tier(tier=2, fact_count=50, correction_rate=0.0, lead_median_s=99999) is None


def test_tier5_cannot_be_demoted_further():
    assert _suggest_tier(tier=5, fact_count=20, correction_rate=0.9, lead_median_s=None) is None


# ---- computation over the ledger ----


def test_stats_measure_yield_and_correction_rate(seeded):
    _source(seeded, "s-media", 4)
    for _ in range(9):
        _fact(seeded, "s-media", 4, observed_at=_recent(2))
    bad = _fact(seeded, "s-media", 4, observed_at=_recent(3))
    _fact(seeded, "s1", 1, observed_at=_recent(1), supersedes=bad)  # official correction

    stats = {s["source_id"]: s for s in compute_source_stats(seeded)}
    media = stats["s-media"]
    assert media["fact_count"] == 10
    assert media["correction_rate"] == 0.1  # 1 of 10 corrected by a higher tier
    assert media["yield_per_day"] == round(10 / 30, 3)

    row = seeded.execute("SELECT * FROM source_stats WHERE source_id = 's-media'").fetchone()
    assert row["superseded_rate"] == 0.1
    assert row["window_days"] == 30


def test_lead_time_measured_against_official_record(seeded):
    _source(seeded, "s-fast", 4)
    _fact(seeded, "s-fast", 4, kind="start", observed_at=_recent(5))
    _fact(seeded, "s1", 1, kind="start", observed_at=_recent(3))  # official, 2h later

    stats = {s["source_id"]: s for s in compute_source_stats(seeded)}
    lead = stats["s-fast"]["lead_median_s"]
    assert lead is not None and 7000 < lead < 7400  # ~2h ahead of official


def test_promotion_suggestion_lands_in_the_table(seeded):
    _source(seeded, "s-scoop", 4)
    for i in range(MIN_FACTS_TO_JUDGE + 2):
        _fact(seeded, "s-scoop", 4, kind=f"k{i}", observed_at=_recent(6))
        _fact(seeded, "s1", 1, kind=f"k{i}", observed_at=_recent(3))  # official 3h later

    compute_source_stats(seeded)
    row = seeded.execute("SELECT * FROM source_stats WHERE source_id = 's-scoop'").fetchone()
    assert row["suggested_tier"] == 3
    assert row["lead_time_median_s"] > 900


def test_back_dated_article_does_not_count_as_a_scoop(seeded):
    """An article published today about a week-old event is not a lead: we
    learned it AFTER the official record, whatever its observed_at says."""
    _source(seeded, "s-latecomer", 4)
    _fact(seeded, "s1", 1, kind="start", observed_at=_recent(60), ingested_at=_recent(60))
    _fact(
        seeded,
        "s-latecomer",
        4,
        kind="start",
        observed_at=_recent(100),  # claims an earlier event time…
        ingested_at=_recent(2),  # …but we only received it just now
    )
    stats = {s["source_id"]: s for s in compute_source_stats(seeded)}
    assert stats["s-latecomer"]["lead_median_s"] < 0  # correctly scored as behind


def test_implausible_lead_is_discarded_as_a_dating_artifact(seeded):
    _source(seeded, "s-weird", 4)
    _fact(seeded, "s-weird", 4, kind="start", observed_at=_recent(600), ingested_at=_recent(600))
    _fact(seeded, "s1", 1, kind="start", observed_at=_recent(1), ingested_at=_recent(1))
    stats = {s["source_id"]: s for s in compute_source_stats(seeded)}
    assert stats["s-weird"]["lead_median_s"] is None  # >3d lead ignored, not rewarded


def test_recompute_is_idempotent(seeded):
    _source(seeded, "s-media", 4)
    for _ in range(3):
        _fact(seeded, "s-media", 4, observed_at=_recent(2))
    compute_source_stats(seeded)
    compute_source_stats(seeded)
    n = seeded.execute(
        "SELECT COUNT(*) AS n FROM source_stats WHERE source_id = 's-media'"
    ).fetchone()["n"]
    assert n == 1


# ---- operator approval ----


def test_apply_tier_changes_source_but_not_history(seeded):
    _source(seeded, "s-media", 4)
    old_fact = _fact(seeded, "s-media", 4, observed_at=_recent(2))
    compute_source_stats(seeded)

    result = apply_tier(seeded, "s-media", 3)
    assert result["from_tier"] == 4 and result["to_tier"] == 3
    assert seeded.execute("SELECT tier FROM sources WHERE id='s-media'").fetchone()["tier"] == 3
    # the fact keeps the tier it was written with — no retroactive credibility
    assert seeded.execute("SELECT tier FROM facts WHERE id=?", (old_fact,)).fetchone()["tier"] == 4
    assert (
        seeded.execute(
            "SELECT suggested_tier FROM source_stats WHERE source_id='s-media'"
        ).fetchone()["suggested_tier"]
        is None
    )


def test_apply_tier_rejects_out_of_range(seeded):
    _source(seeded, "s-media", 4)
    with pytest.raises(ValueError):
        apply_tier(seeded, "s-media", 9)


def test_dismiss_clears_suggestion(seeded):
    _source(seeded, "s-media", 4)
    seeded.execute(
        "INSERT INTO source_stats (source_id, window_days, computed_at, suggested_tier)"
        " VALUES ('s-media', 30, ?, 3)",
        (NOW,),
    )
    dismiss_suggestion(seeded, "s-media")
    assert (
        seeded.execute(
            "SELECT suggested_tier FROM source_stats WHERE source_id='s-media'"
        ).fetchone()["suggested_tier"]
        is None
    )
