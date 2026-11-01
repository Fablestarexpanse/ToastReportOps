"""Year backfill: a fresh install learns the year's closed fires in one pass.

Without them nothing can attach to a closed fire and a year-to-date count is
meaningless. Everything goes through the normal ingest path.
"""

from __future__ import annotations

from toast.pipeline import fire_search

ITEMS = [
    {
        "UniqueId": "aaaa-norte",
        "Name": "Norte Fire",
        "County": "Orange",
        "Type": "Wildfire",
        "AcresBurned": 570.0,
        "PercentContained": 100.0,
        "Latitude": 33.8,
        "Longitude": -117.8,
        "IsActive": False,
        "Started": "2026-10-16T12:00:00Z",
        "Updated": "2026-10-20T12:00:00Z",
    },
    {
        "UniqueId": "bbbb-fine",
        "Name": "Fine Fire",
        "County": "Stanislaus",
        "Type": "Wildfire",
        "AcresBurned": 102.0,
        "PercentContained": 100.0,
        "Latitude": 37.5,
        "Longitude": -121.0,
        "IsActive": False,
        "Started": "2026-10-12T12:00:00Z",
        "Updated": "2026-10-14T12:00:00Z",
    },
]


OTHER_YEAR = {
    **ITEMS[0],
    "UniqueId": "cccc-old",
    "Name": "Old Fire",
    "Started": "2025-07-01T12:00:00Z",
    "Updated": "2025-07-03T12:00:00Z",
}


NOT_A_FIRE = {**ITEMS[0], "UniqueId": "dddd-hazmat", "Name": "Tanker Spill", "Type": "Hazmat"}


def _stub_catalog(monkeypatch, asked):
    """Behaves like the real endpoint: `year=` returns only what is active, and only
    `inactive=true` carries closed fires, for every year."""

    def fake_fetch(adapter, *, inactive=False, year=None):
        asked.append((inactive, year))
        if year is not None or not inactive:
            return []
        return [*ITEMS, OTHER_YEAR, NOT_A_FIRE]

    monkeypatch.setattr(fire_search, "fetch_incidents", fake_fetch)


def test_backfill_creates_only_that_years_closed_fires_from_a_single_fetch(
    db, tmp_path, monkeypatch
):
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    asked: list[tuple[bool, int | None]] = []
    _stub_catalog(monkeypatch, asked)

    out = fire_search.import_year(db, 2026)

    assert len(asked) == 1
    assert out == {"year": 2026, "seen": 2, "imported": 2, "already": 0}
    rows = db.execute("SELECT canonical_name, status FROM incidents ORDER BY 1").fetchall()
    assert [tuple(r) for r in rows] == [("Fine Fire", "contained"), ("Norte Fire", "contained")]


def test_historical_search_finds_closed_fires_for_a_year(db, monkeypatch):
    _stub_catalog(monkeypatch, [])
    found = fire_search.search_fires(db, year=2026)
    assert {r["name"] for r in found} == {"Norte Fire", "Fine Fire", "Tanker Spill"}
    assert [r["name"] for r in fire_search.search_fires(db, year=2025)] == ["Old Fire"]


def test_preview_says_what_a_backfill_would_do_and_writes_nothing(db, monkeypatch):
    _stub_catalog(monkeypatch, [])
    out = fire_search.preview_year(db, 2026)
    assert out == {"year": 2026, "listed": 2, "new": 2, "already": 0, "acres": 672}
    assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM envelopes").fetchone()[0] == 0


def test_preview_counts_what_is_already_in_the_ledger(db, tmp_path, monkeypatch):
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    _stub_catalog(monkeypatch, [])
    fire_search.import_year(db, 2026)
    out = fire_search.preview_year(db, 2026)
    assert (out["new"], out["already"]) == (0, 2)


def test_backfill_run_twice_adds_nothing(db, tmp_path, monkeypatch):
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    _stub_catalog(monkeypatch, [])

    fire_search.import_year(db, 2026)
    facts = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    out = fire_search.import_year(db, 2026)

    assert out == {"year": 2026, "seen": 2, "imported": 0, "already": 2}
    assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == facts
