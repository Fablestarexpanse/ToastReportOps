# Migrations

SQL files applied in numeric order at startup by `core/db.py::apply_migrations`.
A `schema_migrations` version gate makes application idempotent.

## Rules

- **Forward-only.** There are no down-migrations. Rollback = restore from a
  backup snapshot (`backup.py`). Never delete or renumber existing files.
- **Sequential numbering, no gaps.** Next file is `00NN_<name>.sql`.
  `tests/unit/test_migrations.py` asserts the set is exactly `1..N` and applies
  the whole chain on a fresh DB — bump its expected count when you add one.
- **Use `IF NOT EXISTS`** on new tables/indexes/triggers so a re-run is safe.
- **Rebuilding a table** (the create → copy → drop → rename pattern, as in
  0019/0020/0021): wrap in `PRAGMA foreign_keys=OFF`/`ON`, and **re-create every
  trigger the table had, verbatim, inside the same migration**. The append-only
  `facts` triggers must never have a gap — that is the whole point of the
  whitelist-update trigger failing *closed*.

## The append-only ledger (why the triggers matter)

`facts` and `incident_events` are append-only, enforced by triggers, not
convention (0001, 0018–0020, 0035). A fact is never edited in place; a new fact
`supersedes` the old one. If you add a column to `facts`, you must extend the
`facts_limited_update` whitelist trigger — it rejects any UPDATE that changes a
non-whitelisted column, so a forgotten column becomes *immutable*, which is the
safe failure. Test on a copy of a real DB before shipping a `facts` change.
