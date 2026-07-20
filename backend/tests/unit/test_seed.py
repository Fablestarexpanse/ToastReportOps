"""Seed creates official + curated media sources, idempotently."""

from toast.seed import seed


def test_seed_creates_official_and_media_sources(db):
    created = seed(db)
    names = {r["name"] for r in db.execute("SELECT name FROM sources")}
    assert "CAL FIRE active incidents" in names  # official
    assert "InciWeb incidents (national)" in names  # official-ish media
    assert "Google News: California wildfire" in names  # aggregator
    assert "Google News: Butte County fire" in names  # operator's county
    # media feeds use the rss adapter and are enabled
    media = db.execute(
        "SELECT enabled, adapter FROM sources WHERE name = 'Google News: California wildfire'"
    ).fetchone()
    assert media["adapter"] == "rss" and media["enabled"] == 1
    # general news + social sources are seeded too
    assert "LA Times — California" in names
    assert "Mastodon #wildfire" in names
    assert "Reddit r/California (fire search)" in names
    assert len(created) >= 16


def test_seed_is_idempotent(db):
    seed(db)
    n1 = db.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
    assert seed(db) == []  # second run creates nothing
    n2 = db.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
    assert n1 == n2
