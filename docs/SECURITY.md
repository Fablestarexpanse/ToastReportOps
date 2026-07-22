# Security & Safety

Non-negotiable requirements. Anything here that conflicts with convenience wins.

## Threat model

| Threat | Vector | Mitigation |
|---|---|---|
| Prompt injection | scraped pages, social posts, transcripts, EXIF/image metadata containing instructions aimed at the LLM extractor | delimited data blocks, instruction-refusal contract, schema-validated output, adversarial fixture tests (Phase 2 acceptance) |
| SQL injection | ingested text reaching queries | parameterized queries everywhere; no string-built SQL |
| Command/path injection | ingested text reaching shell commands or file paths | ingested content never interpolated into either; raw files stored under content-hash names only |
| SSRF | attacker-controlled URLs in feeds/posts pointing at private services | resolve DNS then check IP against private/link-local/loopback ranges before connecting; re-check on redirects; allowlist/denylist per fetcher |
| Resource exhaustion | huge/slow downloads, decompression bombs, malformed media | size caps, timeouts, content-type validation, parsing of untrusted files (images, PDFs) in a resource-limited subprocess |
| Stored XSS | ingested text rendered in operator UI or public site | sanitize before storage AND escape at render; React default escaping, no `dangerouslySetInnerHTML` for ingested content |
| Credential theft | secrets in logs, in git, on the public site | `.env` git-ignored; structlog scrubber; social credentials are held locally (credentials table + env) and used only by worker-side publisher adapters — there is no separate `publisher` service, so the real boundary is that publishing requires operator approval via the draft state machine, not process isolation; public site is static with zero secrets |
| Unauthorized publish | anything reaching the public without operator approval | approval state machine enforced by SQLite triggers — `published` requires `approved_by`/`approved_at`; publish actions are operator-initiated only |
| Data loss (incl. house fire) | disk failure, theft, wildfire | Litestream → B2 continuous replication, nightly `VACUUM INTO` snapshots, raw-archive sync, drilled restore procedure |

## Prompt-injection posture

The LLM extractor processes hostile content by design. Rules:

1. Ingested content appears only inside clearly delimited data sections of prompts,
   never in system prompts.
2. Every extraction prompt carries an explicit contract: the content may contain
   instructions; they are data to be reported, never followed.
3. Extractor output must validate against a pydantic schema (typed fields, enums,
   ranges) before anything is written; free-form output is discarded.
4. The extractor has no tools — it cannot fetch URLs, run code, or write anywhere
   except through the validated schema.
5. Adversarial fixtures (embedded instructions, fake system messages, exfil attempts)
   are a permanent part of the test suite; a regression fails CI.
6. Evacuation boundaries are never LLM-processed content: stored verbatim from
   official sources, rendered verbatim, deep-linked with capture timestamp.

## Operator UI exposure

- API and UI bind to 127.0.0.1 by default.
- If ever exposed: single-operator password + server-side session, rate-limited
  login, CSRF tokens on every state-changing route.
- The public site is a static export — no server, no attack surface.

## Secrets strategy

- Desktop now: `.env` file (git-ignored), loaded by pydantic-settings; `.env.example`
  documents every variable with a placeholder.
- Server later: docker secrets or systemd credentials; same variable names, no code change.
- Logs: structlog processor redacts values of known secret keys; review before adding
  any new credential.

## Legal hygiene

- robots.txt respected by default; per-source override is an explicit config field
  set by the operator (`sources.robots_override`), never automatic.
- Scanner audio: short clips stored for analysis/reporting, never restreamed.
  Broadcastify TOS conflict is flagged in ROADMAP.md — resolve before Phase 3
  (own-SDR trunk-recorder is the clean path).
- Image credits preserved (`media.credit`) and displayed.
- Every evac-related public item deep-links its official source with capture
  timestamp and a "verify with officials" notice.
- Per-source `tos_notes` field records the legal posture and review date.

## Dependency hygiene

- Pinned versions and lockfiles (`uv.lock`, `package-lock.json`), Dependabot enabled,
  minimal dependency footprint — prefer stdlib and small libraries over frameworks.
