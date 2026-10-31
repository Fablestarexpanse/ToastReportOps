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


def test_if_empty_seeds_a_new_database_once_and_never_re_adds(tmp_path, monkeypatch, capsys):
    """The installer's re-runs must not resurrect a source the operator removed."""
    from toast.core.db import connect
    from toast.seed import main

    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOAST_DB_PATH", str(tmp_path / "toast.db"))
    main(["--if-empty"])
    conn = connect(tmp_path / "toast.db")
    total = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    assert total > 0
    victim = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    conn.execute("DELETE FROM sources WHERE id = ?", (victim,))
    conn.commit()
    capsys.readouterr()
    main(["--if-empty"])
    assert "already has" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM sources WHERE id = ?", (victim,)).fetchone() is None
