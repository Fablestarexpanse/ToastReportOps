"""Activity check: propose, never act.

The rules verdicts are pinned (official-inactive + quiet = close; live heat or
aircraft = keep), the LLM path can only refine within the allowed verdicts,
and closing remains an operator status change that also clears the proposal.
"""

import json

from toast.api import routes
from toast.pipeline import activity


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def _evidence(**over):
    ev = {
        "days_since_last_fact": 4.0,
        "containment_pct": 100,
        "official_status": "inactive",
        "official_containment": 100,
        "hotspots_within_8km": 0,
        "fire_aircraft_last_48h": 0,
    }
    ev.update(over)
    return ev


def test_official_inactive_and_quiet_proposes_close():
    verdict, why = activity._rules_verdict(_evidence())
    assert verdict == "close" and "inactive" in why


def test_live_heat_keeps_a_fire_open_whatever_officials_say():
    verdict, _ = activity._rules_verdict(_evidence(hotspots_within_8km=3))
    assert verdict == "keep"


def test_aircraft_overhead_keeps_it_open():
    verdict, _ = activity._rules_verdict(_evidence(fire_aircraft_last_48h=2))
    assert verdict == "keep"


def test_mixed_signals_are_uncertain():
    verdict, _ = activity._rules_verdict(
        _evidence(official_status="active", official_containment=40, containment_pct=40)
    )
    assert verdict == "uncertain"


def test_sweep_stores_proposals_without_touching_incidents(seeded, monkeypatch):
    monkeypatch.setattr(activity, "_calfire_live", lambda conn: {})
    monkeypatch.setattr(activity, "load_prompt", lambda *a, **k: (_ for _ in ()).throw(Exception()))
    out = activity.check_active(seeded)
    assert out["checked"] >= 1
    assert (
        seeded.execute("SELECT COUNT(*) FROM incidents WHERE status='active'").fetchone()[0] >= 1
    )  # nothing closed by the sweep itself


def test_operator_close_clears_the_proposal_and_logs_the_event(seeded, monkeypatch):
    monkeypatch.setattr(activity, "_calfire_live", lambda conn: {})
    monkeypatch.setattr(activity, "load_prompt", lambda *a, **k: (_ for _ in ()).throw(Exception()))
    activity.check_active(seeded)
    req = Req(seeded)
    out = routes.set_incident_status(req, "i1", {"status": "contained"})
    assert out["status"] == "contained"
    assert (
        seeded.execute("SELECT COUNT(*) FROM activity_proposals WHERE incident_id='i1'")
        .fetchone()[0] == 0
    )
    ev = seeded.execute(
        "SELECT * FROM incident_events WHERE incident_id='i1' AND event='status_change'"
    ).fetchone()
    assert json.loads(ev["detail"])["to"] == "contained"
    # and re-flare works
    assert routes.set_incident_status(req, "i1", {"status": "active"})["status"] == "active"
