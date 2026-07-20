"""Attribution regression guard.

Runs the replay evaluation against the committed gold fixture on every CI run.
This is the safety net for prompt and model changes — both of which the
operator can now make from OPTIONS without touching code.

Thresholds are deliberately asymmetric:
- ANY wrong attribution fails. A fact on the wrong fire corrupts that
  incident's ledger and can reach a published report.
- Recall must hold the Phase 2 acceptance criterion (>=90%). A miss only costs
  manual adjudication, so it is the tolerable failure — but a fall means
  something regressed.
"""

from toast.eval.attribution_eval import load_fixture, run_eval

MIN_RECALL = 0.90  # Phase 2 acceptance criterion
BASELINE_RECALL = 0.983  # measured 2026-07-20; allow a little drift, not a slide


def test_no_fact_is_attributed_to_the_wrong_fire(db):
    result = run_eval(db)
    assert result.wrong == 0, f"wrong attributions: {result.errors}"
    assert result.precision == 1.0


def test_recall_meets_the_phase_2_criterion(db):
    result = run_eval(db)
    assert result.recall is not None
    assert result.recall >= MIN_RECALL, (
        f"attribution recall fell to {result.recall} (was {BASELINE_RECALL}); "
        f"misses: {[e for e in result.errors if e['type'] == 'missed']}"
    )


def test_extractor_still_pulls_the_fire_name_out(db):
    """A fact whose text names the fire but whose extraction produced no name
    is an extractor regression, invisible in the scorer's numbers."""
    result = run_eval(db)
    assert result.extractor_miss == 0, result.errors


def test_gold_fixture_is_intact():
    """Guards the measurement itself: a shrunken or unlabelled fixture would
    make every other assertion pass vacuously."""
    fixture = load_fixture()
    assert len(fixture["facts"]) >= 100
    assert len(fixture["incidents"]) >= 10
    assert all(f["truth_incident"] for f in fixture["facts"])
    names = {i["canonical_name"] for i in fixture["incidents"]}
    assert all(f["truth_incident"] in names for f in fixture["facts"])


def test_official_facts_are_not_counted_as_scorer_misses(db):
    """Official records are attributed by id, never scored. Counting them as
    scorer failures understated recall by more than half when this was first
    written."""
    result = run_eval(db)
    assert result.by_reference > 0
    assert result.total < len(load_fixture()["facts"])
