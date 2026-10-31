"""Official lookup: lists verify board candidates, and absence is never invented."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from toast.core.fetch import FetchError
from toast.pipeline import official_lookup


def _days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_ms_days_ago(days: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)


CALFIRE = [
    {
        "Name": "Norte Fire ",
        "County": "Orange",
        "AcresBurned": 570.0,
        "PercentContained": 100.0,
        "Started": _days_ago(15),
        "IsActive": False,
        "Final": True,
    },
    {
        "Name": "Cooley Fire",
        "County": "Mendocino",
        "AcresBurned": 1365.0,
        "PercentContained": 98.0,
        "Started": _days_ago(14),
        "IsActive": True,
        "Final": False,
    },
    {
        "Name": "Railroad Fire",
        "County": "Contra Costa",
        "AcresBurned": 12.0,
        "PercentContained": 100.0,
        "Started": _days_ago(400),
        "IsActive": False,
        "Final": True,
    },
]
WFIGS = {
    "features": [
        {
            "attributes": {
                "IncidentName": "ASPEN",
                "POOCounty": "Mono",
                "IncidentSize": 0.1,
                "PercentContained": None,
                "IncidentTypeCategory": "WF",
                "FireDiscoveryDateTime": _epoch_ms_days_ago(1),
            }
        },
        {
            "attributes": {
                "IncidentName": "Hayden Ridge",
                "POOCounty": "Siskiyou",
                "IncidentSize": 110.0,
                "PercentContained": None,
                "IncidentTypeCategory": "RX",
                "FireDiscoveryDateTime": _epoch_ms_days_ago(3),
            }
        },
    ]
}


def _feeds(monkeypatch, calfire=CALFIRE, wfigs=WFIGS, fail=()):
    def fake_fetch(url, **kw):
        which = "calfire" if "fire.ca.gov" in url else "wfigs"
        if which in fail:
            raise FetchError(f"HTTP 503 from {which}")
        data = calfire if which == "calfire" else wfigs
        return json.dumps(data).encode(), "application/json"

    monkeypatch.setattr(official_lookup, "fetch", fake_fetch)


def test_closed_cal_fire_incident_is_reported_as_closed(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    c = official_lookup.check(db, "Norte Fire", "Orange County")
    assert c["status"] == "closed"
    assert c["source"] == "CAL FIRE"
    assert c["acres"] == 570.0
    assert c["containment"] == 100.0


def test_active_cal_fire_incident_is_reported_as_active(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Cooley Fire", "Mendocino")["status"] == "active"


def test_wfigs_record_of_any_size_counts_as_reported(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    c = official_lookup.check(db, "Aspen Fire", "Mono County")
    assert c["status"] == "reported"
    assert c["source"] == "NIFC"
    assert c["acres"] == 0.1
    assert c["category"] == "WF"


def test_prescribed_category_comes_through(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Hayden Ridge", None)["category"] == "RX"


def test_an_old_fire_with_the_same_name_does_not_match(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Railroad Fire", "Contra Costa County")["status"] == "none"


def test_same_name_in_another_county_does_not_match(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Norte Fire", "Lassen County")["status"] == "none"


def test_unknown_county_still_matches_on_name(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Norte Fire", None)["status"] == "closed"


def test_never_refreshed_is_unknown_not_absent(db):
    assert official_lookup.check(db, "Norte Fire", "Orange")["status"] == "unknown"


def test_old_cache_is_unknown_not_absent(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    db.execute(
        "UPDATE map_cache SET fetched_at = '2020-01-01T00:00:00.000Z' WHERE key = ?",
        (official_lookup.CACHE_KEY,),
    )
    assert official_lookup.check(db, "Norte Fire", "Orange")["status"] == "unknown"


def test_both_lists_down_is_unknown_not_absent(db, monkeypatch):
    _feeds(monkeypatch, fail=("calfire", "wfigs"))
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Anything Fire", None)["status"] == "unknown"


def test_one_list_down_cannot_prove_absence(db, monkeypatch):
    _feeds(monkeypatch, fail=("wfigs",))
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Norte Fire", "Orange")["status"] == "closed"
    # it could be on the list we could not read
    assert official_lookup.check(db, "Aspen Fire", "Mono")["status"] == "unknown"


def test_a_failed_refresh_keeps_the_last_good_list(db, monkeypatch):
    _feeds(monkeypatch)
    official_lookup.refresh(db)
    _feeds(monkeypatch, fail=("calfire",))
    official_lookup.refresh(db)
    assert official_lookup.check(db, "Norte Fire", "Orange")["status"] == "closed"
    assert official_lookup.check(db, "Aspen Fire", "Mono")["status"] == "reported"


def test_stage_is_scheduled_and_registered():
    from toast.pipeline import handlers  # noqa: F401 - registers stages
    from toast.queue.handlers import get
    from toast.scheduler import DAILY_STAGES

    assert DAILY_STAGES["official_lookup"] == 600
    assert get("official_lookup") is not None
