"""Normalize pipeline: incident auto-creation, value-fact supersession chains,
hotspot geo-attachment, alert facts, envelope dedup."""

import json

from toast.core.envelope import Envelope, insert_envelope
from toast.pipeline.normalize import normalize_envelope

NOW = "2026-07-19T12:00:00.000Z"
LATER = "2026-07-19T13:00:00.000Z"


def _report_env(db, acreage, containment, observed, name="FLUME FIRE"):
    env = Envelope(
        source_id="s1",
        external_id="irwin-1",
        kind="report",
        payload={
            "irwin_id": "irwin-1",
            "name": name,
            "type": "WF",
            "acreage": acreage,
            "containment_pct": containment,
            "discovered_at": NOW,
            "modified_at": observed,
            "county": "Butte",
            "lat": 39.76,
            "lon": -121.6,
        },
    )
    return insert_envelope(db, env)


def test_report_creates_incident_with_provenance_and_facts(seeded):
    # a name that does NOT collide with the seeded FLUME FIRE — this test is
    # about the CREATE path; adoption has its own tests below
    env_id = _report_env(seeded, 100.0, 0.0, NOW, name="MOSQUITO FIRE")
    facts = normalize_envelope(seeded, env_id)
    assert len(facts) == 3  # start + acreage + containment

    inc = seeded.execute("SELECT * FROM incidents WHERE irwin_id='irwin-1'").fetchone()
    assert inc is not None and inc["status"] == "active"
    assert inc["canonical_name"] == "MOSQUITO FIRE"
    assert inc["first_detected_fact_id"] is not None  # first-detection provenance

    start = seeded.execute(
        "SELECT * FROM facts WHERE id=?", (inc["first_detected_fact_id"],)
    ).fetchone()
    assert start["kind"] == "start" and start["geo_method"] == "source_fixed"


def test_changed_value_supersedes_unchanged_skips(seeded):
    env1 = _report_env(seeded, 100.0, 0.0, NOW)
    normalize_envelope(seeded, env1)

    # same values later -> no new facts
    env2 = _report_env(seeded, 100.0, 0.0, LATER)
    assert normalize_envelope(seeded, env2) == []

    # grown fire -> acreage fact superseding the original
    env3 = _report_env(seeded, 350.0, 10.0, LATER)
    facts = normalize_envelope(seeded, env3)
    assert len(facts) == 2  # acreage + containment changed

    chain = seeded.execute(
        """SELECT f.acreage, f.supersedes, prev.acreage AS prev_acreage FROM facts f
           LEFT JOIN facts prev ON prev.id = f.supersedes
           WHERE f.kind='acreage' AND f.supersedes IS NOT NULL"""
    ).fetchone()
    assert chain["acreage"] == 350.0 and chain["prev_acreage"] == 100.0


def test_older_report_never_supersedes(seeded):
    env1 = _report_env(seeded, 350.0, None, LATER)
    normalize_envelope(seeded, env1)
    stale_env = _report_env(seeded, 100.0, None, NOW)  # older observed_at
    assert normalize_envelope(seeded, stale_env) == []


def test_envelope_dedup(seeded):
    assert _report_env(seeded, 100.0, 0.0, NOW) is not None
    assert _report_env(seeded, 100.0, 0.0, NOW) is None  # identical content dropped


def test_hotspot_attaches_to_nearby_incident(seeded):
    env1 = _report_env(seeded, 100.0, 0.0, NOW)
    normalize_envelope(seeded, env1)
    inc = seeded.execute("SELECT id FROM incidents WHERE irwin_id='irwin-1'").fetchone()

    near = Envelope(
        source_id="s1",
        external_id="h1",
        kind="hotspot",
        payload={"lat": 39.80, "lon": -121.58, "frp": 12.0, "satellite": "N", "observed_at": LATER},
    )
    fid = normalize_envelope(seeded, insert_envelope(seeded, near))[0]
    fact = seeded.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
    assert fact["incident_id"] == inc["id"] and fact["kind"] == "other"

    far = Envelope(
        source_id="s1",
        external_id="h2",
        kind="hotspot",
        payload={"lat": 41.9, "lon": -114.2, "frp": 5.0, "satellite": "N", "observed_at": LATER},
    )
    fid2 = normalize_envelope(seeded, insert_envelope(seeded, far))[0]
    fact2 = seeded.execute("SELECT * FROM facts WHERE id=?", (fid2,)).fetchone()
    assert fact2["incident_id"] is None and fact2["kind"] == "start"


def test_alert_becomes_unattributed_weather_fact(seeded):
    env = Envelope(
        source_id="s1",
        external_id="a1",
        kind="alert",
        payload={
            "event": "Red Flag Warning",
            "headline": "Red Flag Warning until 8 PM",
            "area_desc": "Butte County",
            "sent": NOW,
            "severity": "Severe",
        },
    )
    fid = normalize_envelope(seeded, insert_envelope(seeded, env))[0]
    fact = seeded.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
    assert fact["kind"] == "weather" and fact["incident_id"] is None
    assert "Red Flag Warning" in fact["content"]
    assert json.loads(fact["structured"])["area_desc"] == "Butte County"


class TestCrossSourceAdoption:
    """The same fire from CAL FIRE and WFIGS must stay ONE incident."""

    def test_wfigs_adopts_the_calfire_incident(self, seeded):
        from toast.pipeline.incidents import upsert_incident

        first, created = upsert_incident(
            seeded, ref_col="calfire_id", ref_id="cf-park", name="Park Fire",
            lat=39.8, lon=-121.6, county="Butte", via="calfire",
        )
        assert created
        second, created2 = upsert_incident(
            seeded, ref_col="irwin_id", ref_id="irwin-park", name="PARK Fire",
            lat=39.85, lon=-121.55, county="Butte", via="wfigs",
        )
        assert not created2 and second == first
        row = seeded.execute(
            "SELECT calfire_id, irwin_id FROM incidents WHERE id=?", (first,)
        ).fetchone()
        assert row["calfire_id"] == "cf-park" and row["irwin_id"] == "irwin-park"

    def test_same_name_far_away_is_a_different_fire(self, seeded):
        from toast.pipeline.incidents import upsert_incident

        a, _ = upsert_incident(
            seeded, ref_col="calfire_id", ref_id="cf-creek", name="Creek Fire",
            lat=37.2, lon=-119.3, county="Fresno", via="calfire",
        )
        b, created = upsert_incident(
            seeded, ref_col="irwin_id", ref_id="irwin-creek", name="Creek Fire",
            lat=40.9, lon=-122.4, county="Shasta", via="wfigs",
        )
        assert created and b != a


class TestWfigsCreationGate:
    """Raw dispatch noise must not become incidents; real fires must."""

    def _env(self, db, *, name, acreage, typ="WF", personnel=0, ext):
        env = Envelope(
            source_id="s1", external_id=ext, kind="report",
            payload={"irwin_id": ext, "name": name, "type": typ,
                     "acreage": acreage, "personnel": personnel,
                     "modified_at": NOW, "county": "Kern",
                     "lat": 35.3, "lon": -118.7},
        )
        return insert_envelope(db, env)

    def test_tiny_dispatch_ticket_is_ignored(self, seeded):
        facts = normalize_envelope(seeded, self._env(
            seeded, name="LAC-254773", acreage=0.1, ext="irwin-lac"))
        assert facts == []
        assert seeded.execute(
            "SELECT COUNT(*) FROM incidents WHERE irwin_id='irwin-lac'"
        ).fetchone()[0] == 0

    def test_rx_burn_is_ignored_whatever_its_size(self, seeded):
        facts = normalize_envelope(seeded, self._env(
            seeded, name="KRRD WIDE JACKPOT RX BURN", acreage=900, typ="RX", ext="irwin-rx"))
        assert facts == []

    def test_a_real_fire_clears_the_gate(self, seeded):
        facts = normalize_envelope(seeded, self._env(
            seeded, name="BEAR FIRE", acreage=45, ext="irwin-bear"))
        assert facts
        assert seeded.execute(
            "SELECT COUNT(*) FROM incidents WHERE irwin_id='irwin-bear'"
        ).fetchone()[0] == 1

    def test_heavy_response_clears_the_gate_even_when_small(self, seeded):
        facts = normalize_envelope(seeded, self._env(
            seeded, name="SUMMIT FIRE", acreage=2, personnel=80, ext="irwin-summit"))
        assert facts

    def test_updates_to_a_known_fire_always_land(self, seeded):
        normalize_envelope(seeded, self._env(
            seeded, name="BEAR FIRE", acreage=45, ext="irwin-known"))
        facts = normalize_envelope(seeded, Envelope and insert_envelope(seeded, Envelope(
            source_id="s1", external_id="irwin-known", kind="report",
            payload={"irwin_id": "irwin-known", "name": "BEAR FIRE", "type": "WF",
                     "acreage": 3, "modified_at": LATER, "county": "Kern",
                     "lat": 35.3, "lon": -118.7})))
        # acreage REVISED DOWN below the gate — still an update, never dropped
        assert facts


class TestOfficialFirstValues:
    """The current number comes from the most trusted tier, not the loudest
    timestamp — and a fact claiming to be from the future never wins."""

    def _fact(self, db, *, acreage, tier, observed, fid):
        db.execute(
            """INSERT INTO facts (id, incident_id, envelope_id, source_id, tier,
                 kind, content, acreage, confidence, observed_at, ingested_at)
               VALUES (?, 'i1', 'e1', 's1', ?, 'acreage', ?, ?, 0.9, ?, ?)""",
            (fid, tier, f"{acreage} acres", acreage, observed, observed),
        )

    def test_official_beats_newer_media_number(self, seeded):
        from toast.pipeline.ledger import latest_fact

        self._fact(seeded, acreage=5500, tier=1, observed="2026-07-19T10:00:00.000Z", fid="fa")
        self._fact(seeded, acreage=9000, tier=3, observed="2026-07-19T12:00:00.000Z", fid="fb")
        assert latest_fact(seeded, "i1", "acreage")["acreage"] == 5500

    def test_newest_official_wins_within_the_official_group(self, seeded):
        from toast.pipeline.ledger import latest_fact

        self._fact(seeded, acreage=5500, tier=1, observed="2026-07-19T10:00:00.000Z", fid="fa")
        self._fact(seeded, acreage=6200, tier=2, observed="2026-07-19T12:00:00.000Z", fid="fb")
        assert latest_fact(seeded, "i1", "acreage")["acreage"] == 6200

    def test_future_stamped_fact_ranks_last(self, seeded):
        from toast.pipeline.ledger import latest_fact

        self._fact(seeded, acreage=950, tier=3, observed="2099-01-01T00:00:00.000Z", fid="fa")
        self._fact(seeded, acreage=700, tier=3, observed="2026-07-19T12:00:00.000Z", fid="fb")
        assert latest_fact(seeded, "i1", "acreage")["acreage"] == 700

    def test_media_number_stands_when_it_is_all_there_is(self, seeded):
        from toast.pipeline.ledger import latest_fact

        self._fact(seeded, acreage=300, tier=4, observed="2026-07-19T12:00:00.000Z", fid="fa")
        assert latest_fact(seeded, "i1", "acreage")["acreage"] == 300


class TestFactIdempotence:
    """A source re-polled and re-extracted a claim it already gave us: the same
    words must not stack a second live row into the intake feed."""

    def _news(self, db, content, *, incident_id=None, kind="other", supersedes=None):
        from toast.pipeline.ledger import write_fact

        return write_fact(
            db,
            incident_id=incident_id,
            envelope_id="e1",
            source_id="s1",
            tier=4,
            content=content,
            kind=kind,
            observed_at=NOW,
            supersedes=supersedes,
        )

    def test_same_source_identical_claim_is_not_duplicated(self, seeded):
        a = self._news(seeded, "The Biscar Fire is 42% contained.")
        b = self._news(seeded, "The Biscar Fire is 42% contained.")
        assert a == b  # handed back the fact we already hold
        n = seeded.execute(
            "SELECT COUNT(*) FROM facts WHERE content = 'The Biscar Fire is 42% contained.'"
        ).fetchone()[0]
        assert n == 1

    def test_incidentless_news_claims_dedup_too(self, seeded):
        a = self._news(seeded, "A new wildfire was reported in Los Angeles County.")
        b = self._news(seeded, "A new wildfire was reported in Los Angeles County.")
        assert a == b
        assert seeded.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1

    def test_different_content_still_written(self, seeded):
        self._news(seeded, "The Biscar Fire is 42% contained.")
        self._news(seeded, "The Biscar Fire is 55% contained.")
        assert seeded.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2

    def test_other_source_saying_the_same_thing_still_counts(self, seeded):
        seeded.execute(
            "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
            " VALUES ('s2', 'Other', 'scrape', 'rss', 4, ?, ?)",
            (NOW, NOW),
        )
        self._news(seeded, "The Biscar Fire is 42% contained.")
        seeded.execute(
            "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content,"
            " kind, confidence, observed_at, ingested_at) VALUES"
            " ('f2', NULL, 'e1', 's2', 4, 'The Biscar Fire is 42% contained.', 'other',"
            " 0.9, ?, ?)",
            (NOW, NOW),
        )
        # cross-source agreement is corroboration, not a duplicate: both rows live
        assert seeded.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2

    def test_a_superseding_write_is_never_blocked(self, seeded):
        first = self._news(seeded, "60 acres", kind="acreage")
        # a genuine correction carries supersedes and must go through even though
        # the guard would otherwise see a live row for this source+kind
        second = self._news(seeded, "60 acres", kind="acreage", supersedes=first)
        assert second != first
        assert seeded.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
