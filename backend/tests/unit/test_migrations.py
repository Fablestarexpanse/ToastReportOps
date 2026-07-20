from toast.core.db import apply_migrations, connect


def test_migrations_apply_and_are_idempotent(tmp_path):
    conn = connect(tmp_path / "m.db")
    first = apply_migrations(conn)
    assert first == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    assert apply_migrations(conn) == []  # second run is a no-op

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "sources",
        "source_stats",
        "raw_blobs",
        "envelopes",
        "incidents",
        "incident_aliases",
        "incident_events",
        "facts",
        "media",
        "audio_clips",
        "drafts",
        "publications",
        "candidates",
        "candidate_signals",
        "alert_rules",
        "alert_deliveries",
        "jobs",
        "fact_assessments",
        "schema_migrations",
    }
    assert expected <= tables


def test_wal_and_foreign_keys(tmp_path):
    conn = connect(tmp_path / "w.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
