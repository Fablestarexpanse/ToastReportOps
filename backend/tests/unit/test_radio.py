"""Scanner ingest + transcription.

The doctrine under test: recordings are evidence (CAS, indexed, never lost);
transcripts are never fabricated — no Whisper endpoint means visibly pending;
and a fire-worded transmission becomes a T5 WATCH signal that the tier system
keeps out of drafts and evac displays.
"""

import json

import httpx

from toast.core.config import Settings
from toast.pipeline import radio


def _settings(tmp_path, whisper=""):
    return Settings(toast_data_dir=str(tmp_path), whisper_url=whisper)


def _drop_call(tmp_path, name="call1", meta=None):
    box = tmp_path / "radio" / "inbox"
    box.mkdir(parents=True, exist_ok=True)
    (box / f"{name}.wav").write_bytes(b"RIFF-fake-audio-" + name.encode())
    if meta is not None:
        (box / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")


class TestIngest:
    def test_call_with_sidecar_lands_in_cas_and_index(self, seeded, tmp_path):
        _drop_call(
            tmp_path,
            meta={
                "system": "calfire-vhf",
                "talkgroup": "CALFIRE TAC 2",
                "freq_hz": 151355000,
                "start_time": 1752900000,
                "duration_s": 8.4,
            },
        )
        out = radio.ingest_inbox(seeded, _settings(tmp_path))
        assert out["ingested"] == 1
        call = seeded.execute("SELECT * FROM radio_calls").fetchone()
        assert call["talkgroup"] == "CALFIRE TAC 2"
        assert call["freq_hz"] == 151355000
        assert call["transcript_state"] == "pending"
        # the recording is in the CAS and the inbox is drained
        blob = seeded.execute(
            "SELECT * FROM raw_blobs WHERE sha256 = ?", (call["audio_sha"],)
        ).fetchone()
        assert blob is not None
        assert not list((tmp_path / "radio" / "inbox").iterdir())

    def test_audio_without_sidecar_still_ingests(self, seeded, tmp_path):
        _drop_call(tmp_path, meta=None)
        assert radio.ingest_inbox(seeded, _settings(tmp_path))["ingested"] == 1

    def test_broken_sidecar_never_blocks_the_recording(self, seeded, tmp_path):
        box = tmp_path / "radio" / "inbox"
        box.mkdir(parents=True)
        (box / "x.wav").write_bytes(b"audio")
        (box / "x.json").write_text("{not json", encoding="utf-8")
        assert radio.ingest_inbox(seeded, _settings(tmp_path))["ingested"] == 1


class TestTranscription:
    def _ingested(self, seeded, tmp_path, meta=None):
        _drop_call(tmp_path, meta=meta or {"talkgroup": "CALFIRE TAC 2"})
        radio.ingest_inbox(seeded, _settings(tmp_path))
        return seeded.execute("SELECT id FROM radio_calls").fetchone()["id"]

    def test_no_endpoint_stays_visibly_pending(self, seeded, tmp_path):
        call_id = self._ingested(seeded, tmp_path)
        out = radio.transcribe_call(seeded, call_id, _settings(tmp_path, whisper=""))
        assert out["state"] == "pending"
        row = seeded.execute("SELECT transcript_state, transcript FROM radio_calls").fetchone()
        assert row["transcript_state"] == "pending" and row["transcript"] is None

    def _fake_whisper(self, text):
        def handler(request):
            return httpx.Response(200, json={"text": text})

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_fire_worded_transcript_feeds_watch_as_t5(self, seeded, tmp_path):
        call_id = self._ingested(seeded, tmp_path)
        client = self._fake_whisper("Vegetation fire off Clark Road, column visible.")
        out = radio.transcribe_call(
            seeded, call_id, _settings(tmp_path, whisper="http://whisper:8000"), client
        )
        assert out["state"] == "done" and out["watch_fact_id"]
        fact = seeded.execute(
            "SELECT * FROM facts WHERE id = ?", (out["watch_fact_id"],)
        ).fetchone()
        assert fact["tier"] == 5  # quarantined by tier everywhere downstream
        assert fact["incident_id"] is None  # unattributed: exactly what WATCH reads
        assert "Clark Road" in fact["content"]
        src = seeded.execute("SELECT * FROM sources WHERE id = ?", (fact["source_id"],)).fetchone()
        assert src["adapter"] == "radio" and src["tier"] == 5

    def test_routine_traffic_transcribes_without_feeding_watch(self, seeded, tmp_path):
        call_id = self._ingested(seeded, tmp_path)
        client = self._fake_whisper("Unit 12 returning to station for fuel.")
        out = radio.transcribe_call(
            seeded, call_id, _settings(tmp_path, whisper="http://whisper:8000"), client
        )
        assert out["state"] == "done" and out["watch_fact_id"] is None
        assert seeded.execute("SELECT COUNT(*) FROM facts WHERE tier = 5").fetchone()[0] == 0

    def test_whisper_failure_is_marked_not_retried_forever(self, seeded, tmp_path):
        call_id = self._ingested(seeded, tmp_path)

        def boom(request):
            return httpx.Response(500, text="loading model")

        client = httpx.Client(transport=httpx.MockTransport(boom))
        out = radio.transcribe_call(
            seeded, call_id, _settings(tmp_path, whisper="http://whisper:8000"), client
        )
        assert out["state"] == "failed"
        assert seeded.execute("SELECT transcript_state FROM radio_calls").fetchone()[0] == "failed"
