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
        "aircraft_overhead_6h": 0,
    }
    ev.update(over)
    return ev


def test_official_inactive_and_quiet_proposes_close():
    verdict, why = activity._rules_verdict(_evidence())
    assert verdict == "close" and "inactive" in why


def test_live_heat_on_an_active_fire_keeps_it_open():
    verdict, _ = activity._rules_verdict(
        _evidence(hotspots_within_8km=3, official_status="active",
                  official_containment=40, containment_pct=40)
    )
    assert verdict == "keep"


def test_live_heat_against_official_inactive_needs_eyes():
    """Officials say out, satellite says heat: neither wins silently."""
    verdict, why = activity._rules_verdict(_evidence(hotspots_within_8km=3))
    assert verdict == "uncertain" and "officials" in why


def test_aircraft_overhead_keeps_it_open():
    verdict, _ = activity._rules_verdict(
        _evidence(aircraft_overhead_6h=2, official_status="active",
                  official_containment=40, containment_pct=40)
    )
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


class TestAutoClose:
    def _prep(self, seeded):
        seeded.execute("UPDATE incidents SET calfire_id = 'cf-1' WHERE id = 'i1'")

    def test_official_inactive_auto_closes_with_the_authority_logged(self, seeded, monkeypatch):
        self._prep(seeded)
        monkeypatch.setattr(
            activity, "_calfire_live",
            lambda conn: {"cf-1": {"is_active": False, "containment": 100, "updated": None}},
        )
        monkeypatch.setattr(
            activity, "load_prompt", lambda *a, **k: (_ for _ in ()).throw(Exception())
        )
        out = activity.check_active(seeded)
        assert out["auto_closed"] == 1
        assert (
            seeded.execute("SELECT status FROM incidents WHERE id='i1'").fetchone()[0]
            == "contained"
        )
        ev = seeded.execute(
            "SELECT actor, detail FROM incident_events WHERE incident_id='i1'"
        ).fetchone()
        assert ev["actor"] == "calfire-official"
        assert "inactive" in ev["detail"]

    def test_live_heat_blocks_the_auto_close(self, seeded, monkeypatch):
        """Officials say out but the satellite disagrees: a human decides."""
        self._prep(seeded)
        seeded.execute("UPDATE incidents SET lat=40.9, lon=-121.3 WHERE id='i1'")
        seeded.execute(
            "INSERT INTO map_cache (key, payload, fetched_at) VALUES ('hotspots:ca', ?, ?)",
            ('{"hotspots": [{"lat": 40.9, "lon": -121.3}]}', "2026-07-19T00:00:00.000Z"),
        )
        monkeypatch.setattr(
            activity, "_calfire_live",
            lambda conn: {"cf-1": {"is_active": False, "containment": 100, "updated": None}},
        )
        monkeypatch.setattr(
            activity, "load_prompt", lambda *a, **k: (_ for _ in ()).throw(Exception())
        )
        out = activity.check_active(seeded)
        assert out["auto_closed"] == 0
        assert (
            seeded.execute("SELECT status FROM incidents WHERE id='i1'").fetchone()[0]
            == "active"
        )


class TestEvacZoneResync:
    def test_a_new_evac_order_queues_an_immediate_zone_sync(self, seeded):
        from toast.pipeline.ledger import write_fact

        write_fact(
            seeded, incident_id="i1", envelope_id="e1", source_id="s1", tier=1,
            content="Evacuation order issued for zone 14.", kind="evac",
            evac_status="order", confidence=0.95,
            observed_at="2026-07-19T00:00:00.000Z",
        )
        jobs = seeded.execute(
            "SELECT payload, run_after FROM jobs WHERE stage='evac_zone_sync'"
        ).fetchall()
        assert len(jobs) == 2  # now, and a follow-up — zones lag the news
        assert any(j["run_after"] is not None for j in jobs)

    def test_a_lifted_status_does_not_spam_the_sync(self, seeded):
        from toast.pipeline.ledger import write_fact

        write_fact(
            seeded, incident_id="i1", envelope_id="e1", source_id="s1", tier=1,
            content="All evacuation orders lifted.", kind="evac",
            evac_status="lifted", confidence=0.95,
            observed_at="2026-07-19T00:00:00.000Z",
        )
        n = seeded.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='evac_zone_sync'"
        ).fetchone()[0]
        assert n == 0


def test_official_out_but_aircraft_overhead_is_uncertain_not_keep():
    verdict, why = activity._rules_verdict(_evidence(aircraft_overhead_6h=1))
    assert verdict == "uncertain"
    assert "officials" in why
