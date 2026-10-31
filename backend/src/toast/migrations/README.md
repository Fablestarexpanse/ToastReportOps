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

## Index (0001–0039)

Each file's header comment is the full story; this is the map. When you add a
migration, add its line here and bump the count in `tests/unit/test_migrations.py`.

| # | What it adds |
|---|---|
| 0001 | Schema v1: sources, envelopes, raw blobs, facts (append-only, bi-temporal), incidents, drafts + the publish-requires-approval trigger, publications, jobs, candidates |
| 0002 | Per-source poll cursor |
| 0003 | Incident type (WF wildfire, RX prescribed burn, …) |
| 0004 | Recomputable per-fact assessments |
| 0005 | CAL FIRE cross-reference id on incidents |
| 0006 | Draft headline |
| 0007 | Draft lineage (social rewrite → parent) |
| 0008 | Source discovery suggestions |
| 0009 | Operator notes/tasks and FOLLOW flag |
| 0010 | Runtime app settings (key/value) |
| 0011 | Adjudication triage |
| 0012 | Watch-board dispositions |
| 0013 | Operator-entered API credentials |
| 0014 | USGS GNIS place names |
| 0015 | Operator-edited prompt overrides |
| 0016 | NWS weather cache per incident |
| 0017 | External map-layer cache (`map_cache`, also used for alert baselines) |
| 0018 | Append-only facts made real (whitelist update trigger) |
| 0019 | Enum CHECKs that actually constrain (`x IS NULL OR x IN …`) |
| 0020 | Timestamps must be parseable |
| 0021 | Publication "unknown" outcome |
| 0022 | Raw blobs' offsite-copy state |
| 0023–0026 | Aircraft positions, role, photo cache and bytes, military flag |
| 0027 | Perimeter progression history |
| 0028 | Radio calls from the operator's SDR |
| 0029 | Scheduled, channel-selected release |
| 0030 | Rendered incident card on a draft |
| 0031 | AI triage proposals |
| 0032 | Meshtastic mesh state |
| 0033 | Draft archival (orthogonal to status) |
| 0034 | Activity-check proposals |
| 0035 | 07-22 audit hardening |
| 0036 | Scanner channel directory |
| 0037 | Mesh chat as a watched source |
| 0038 | Draft verifier result on drafts (`verify_issues`) |
| 0039 | Daily reservoir snapshots (`reservoir_readings`) |
