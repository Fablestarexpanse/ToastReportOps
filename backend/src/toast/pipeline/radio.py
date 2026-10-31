"""Scanner ingest: the operator's own SDR drops calls in an inbox; we keep them.

The contract is a directory, deliberately: rtl_airband, trunk-recorder and
SDRTrunk can all be configured to emit an audio file per transmission, and a
JSON sidecar (same basename) with whatever metadata the capture tool knows:

    data/radio/inbox/2026-07-21_1930_calfire-tac2.wav
    data/radio/inbox/2026-07-21_1930_calfire-tac2.json
      {"system": "calfire-vhf", "talkgroup": "CALFIRE TAC 2",
       "freq_hz": 151355000, "start_time": 1784650000, "duration_s": 8.4}

Audio without a sidecar still ingests (timestamps from the file); a sidecar
never blocks on missing fields. Bytes go to the CAS like every raw capture —
the recording IS the evidence — and transcription is a separate, pluggable
step (see transcribe_call): a transcript is never fabricated or assumed.

Doctrine: radio traffic is T5 community-grade signal. A fire-worded transcript
feeds the WATCH board and nothing else; it cannot reach drafts or evacuation
displays without official corroboration, enforced by tier like every source.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.core.logging import get_logger
from toast.core.rawstore import store_raw

AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}
FIRE_WORDS = (
    "fire",
    "smoke",
    "vegetation",
    "structure threat",
    "spot over",
    "evac",
    "ic established",
    "working fire",
    "fully involved",
)
TRANSCRIPT_MAX = 4000


def inbox_dir(settings: Settings | None = None) -> Path:
    s = settings or Settings()
    return Path(s.toast_data_dir) / "radio" / "inbox"


def _epoch_iso(v: Any) -> str | None:
    if not isinstance(v, int | float) or v <= 0:
        return None
    return (
        datetime.fromtimestamp(float(v), tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def ingest_inbox(conn: sqlite3.Connection, settings: Settings | None = None) -> dict[str, Any]:
    """Sweep the inbox: store audio in the CAS, index the call, remove the file."""
    s = settings or Settings()
    box = inbox_dir(s)
    if not box.exists():
        box.mkdir(parents=True, exist_ok=True)
        return {"ingested": 0, "note": "inbox created"}

    log = get_logger("radio")
    ingested = skipped = 0
    for audio in sorted(box.iterdir()):
        media_type = AUDIO_TYPES.get(audio.suffix.lower())
        if media_type is None:
            continue  # sidecars and strays are handled with their audio
        sidecar = audio.with_suffix(".json")
        meta: dict[str, Any] = {}
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                meta = {}  # a broken sidecar never blocks the recording
        body = audio.read_bytes()
        if not body:
            skipped += 1
            audio.unlink(missing_ok=True)
            continue
        sha = store_raw(conn, Path(s.toast_data_dir), body, media_type, url=None)
        started = (
            _epoch_iso(meta.get("start_time")) or _epoch_iso(audio.stat().st_mtime) or utcnow()
        )
        conn.execute(
            """INSERT INTO radio_calls
               (id, system, talkgroup, freq_hz, started_at, duration_s,
                audio_sha, audio_type, transcript_state, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                ulid(),
                meta.get("system"),
                meta.get("talkgroup"),
                int(meta["freq_hz"]) if isinstance(meta.get("freq_hz"), int | float) else None,
                started,
                float(meta["duration_s"])
                if isinstance(meta.get("duration_s"), int | float)
                else None,
                sha,
                media_type,
                utcnow(),
            ),
        )
        audio.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        ingested += 1
    conn.commit()
    if ingested:
        log.info("radio_ingested", calls=ingested, skipped=skipped)
    return {"ingested": ingested, "skipped": skipped}


def transcribe_call(
    conn: sqlite3.Connection,
    call_id: str,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Transcribe one call against a local OpenAI-compatible Whisper endpoint.

    No endpoint configured = the call STAYS pending, visibly — an untranscribed
    recording is a fact; an invented transcript would be a lie."""
    s = settings or Settings()
    row = conn.execute("SELECT * FROM radio_calls WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        raise ValueError("unknown call")
    if row["transcript_state"] == "done":
        return {"state": "done", "cached": True}
    whisper = (getattr(s, "whisper_url", "") or "").rstrip("/")
    if not whisper:
        return {"state": "pending", "note": "WHISPER_URL not configured"}

    blob = conn.execute(
        "SELECT path FROM raw_blobs WHERE sha256 = ?", (row["audio_sha"],)
    ).fetchone()
    audio_path = Path(s.toast_data_dir) / blob["path"]
    own = client is None
    client = client or httpx.Client(timeout=300)
    try:
        resp = client.post(
            f"{whisper}/v1/audio/transcriptions",
            files={"file": ("call.wav", audio_path.read_bytes(), row["audio_type"])},
            data={"model": getattr(s, "whisper_model", "") or "whisper-1"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"whisper HTTP {resp.status_code}")
        text = (resp.json().get("text") or "").strip()[:TRANSCRIPT_MAX]
    except Exception as exc:
        conn.execute("UPDATE radio_calls SET transcript_state = 'failed' WHERE id = ?", (call_id,))
        conn.commit()
        get_logger("radio").warning("radio_transcribe_failed", call=call_id, error=str(exc)[:120])
        return {"state": "failed", "error": str(exc)[:120]}
    finally:
        if own:
            client.close()

    fact_id = None
    if text and any(w in text.lower() for w in FIRE_WORDS):
        fact_id = _feed_watch(conn, row, text)
    conn.execute(
        """UPDATE radio_calls SET transcript = ?, transcript_state = 'done',
           watch_fact_id = ? WHERE id = ?""",
        (text or None, fact_id, call_id),
    )
    conn.commit()
    get_logger("radio").info(
        "radio_transcribed", call=call_id, chars=len(text), watched=bool(fact_id)
    )
    return {"state": "done", "chars": len(text), "watch_fact_id": fact_id}


def _radio_source(conn: sqlite3.Connection) -> str:
    """The T5 'own scanner' source row, created once."""
    row = conn.execute("SELECT id FROM sources WHERE adapter = 'radio'").fetchone()
    if row:
        return row["id"]
    sid = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, enabled, tos_notes,
                                created_at, updated_at)
           VALUES (?, 'Own scanner (SDR)', 'audio', 'radio', 5, 1,
                   'Operator-owned receiver. T5: watch signals only, never publishable fact.',
                   ?, ?)""",
        (sid, now, now),
    )
    return sid


def _feed_watch(conn: sqlite3.Connection, call: sqlite3.Row, text: str) -> str:
    """A fire-worded transmission becomes a T5 fact so WATCH can cluster it.

    Kind 'start' with no incident: exactly the shape the watch board reads.
    Tier 5 keeps it quarantined from drafts and evac displays everywhere."""
    source_id = _radio_source(conn)
    env_id = ulid()
    conn.execute(
        """INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,
                                  fetched_at)
           VALUES (?, ?, ?, 'report', ?, ?, ?)""",
        (
            env_id,
            source_id,
            f"radio:{call['id']}",
            json.dumps(
                {
                    "talkgroup": call["talkgroup"],
                    "system": call["system"],
                    "started_at": call["started_at"],
                    "transcript": text,
                }
            ),
            f"radio-{call['id']}",
            utcnow(),
        ),
    )
    fact_id = ulid()
    content = f"[scanner {call['talkgroup'] or 'unknown channel'}] {text}"[:500]
    conn.execute(
        """INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,
                              structured, confidence, observed_at, ingested_at)
           VALUES (?, NULL, ?, ?, 5, ?, 'start', '{}', 0.3, ?, ?)""",
        (fact_id, env_id, source_id, content, call["started_at"], utcnow()),
    )
    return fact_id


def pending_transcriptions(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    return [
        r["id"]
        for r in conn.execute(
            """SELECT id FROM radio_calls WHERE transcript_state = 'pending'
               ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        )
    ]
