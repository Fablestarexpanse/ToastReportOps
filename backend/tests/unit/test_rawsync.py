"""Offsite replication of the raw archive.

The ledger says what was claimed; data/raw/ is what the source actually served
when we read it. A news page that moves is gone, so this half is not
reconstructable — and a ledger whose evidence burned with the house is a ledger
nobody can check.
"""

import httpx
import pytest

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.rawsync import pending, sync_raw

NOW = "2026-07-19T00:00:00.000Z"


@pytest.fixture
def archive(seeded, tmp_path):
    """Three blobs on disk, indexed, none offsite yet."""
    for i, body in enumerate([b"page one", b"page two", b"page three"]):
        import hashlib

        sha = hashlib.sha256(body).hexdigest()
        rel = f"raw/{sha[:2]}/{sha}"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        seeded.execute(
            "INSERT INTO raw_blobs (sha256, media_type, size_bytes, storage, path, fetched_at)"
            " VALUES (?, 'text/html', ?, 'local', ?, ?)",
            (sha, len(body), rel, f"2026-07-19T00:0{i}:00.000Z"),
        )
    return tmp_path


def _settings(tmp_path) -> Settings:
    return Settings(
        b2_key_id="k", b2_application_key="s", b2_bucket="b", toast_data_dir=str(tmp_path)
    )


def _fake_b2(uploaded: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "b2_authorize_account" in url:
            return httpx.Response(
                200,
                json={
                    "authorizationToken": "t",
                    "accountId": "a",
                    "apiInfo": {"storageApi": {"apiUrl": "https://api.example"}},
                },
            )
        if "b2_list_buckets" in url:
            return httpx.Response(200, json={"buckets": [{"bucketId": "bid"}]})
        if "b2_get_upload_url" in url:
            return httpx.Response(
                200, json={"uploadUrl": "https://upload.example/x", "authorizationToken": "u"}
            )
        uploaded.append(request.headers["X-Bz-File-Name"])
        return httpx.Response(200, json={"fileId": "f"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_blobs_are_uploaded_under_their_own_hash(seeded, archive):
    uploaded: list[str] = []
    result = sync_raw(seeded, _settings(archive), client=_fake_b2(uploaded))
    assert result["uploaded"] == 3
    # content-addressed keys mirroring the local layout: raw/<ab>/<sha256>
    assert all(k.startswith("raw/") and len(k.split("/")[-1]) == 64 for k in uploaded)
    assert pending(seeded)["pending"] == 0


def test_a_second_run_uploads_nothing(seeded, archive):
    """Incremental by construction — the key IS the content hash."""
    sync_raw(seeded, _settings(archive), client=_fake_b2([]))
    second: list[str] = []
    result = sync_raw(seeded, _settings(archive), client=_fake_b2(second))
    assert result["uploaded"] == 0
    assert second == []


def test_batch_limit_bounds_a_run(seeded, archive):
    """A first sync of thousands must not stall the queue behind live fire work."""
    uploaded: list[str] = []
    result = sync_raw(seeded, _settings(archive), limit=2, client=_fake_b2(uploaded))
    assert result["uploaded"] == 2
    assert result["pending"] == 1  # the rest waits for the next run


def test_missing_local_file_is_reported_not_recorded_as_offsite(seeded, archive):
    """Recording a blob we could not read as safely stored would be a lie."""
    seeded.execute(
        "INSERT INTO raw_blobs (sha256, media_type, size_bytes, storage, path, fetched_at)"
        " VALUES ('deadbeef', 'text/html', 5, 'local', 'raw/de/deadbeef', ?)",
        (NOW,),
    )
    result = sync_raw(seeded, _settings(archive), client=_fake_b2([]))
    assert result["skipped"] == 1
    assert (
        seeded.execute("SELECT COUNT(*) FROM raw_offsite WHERE sha256 = 'deadbeef'").fetchone()[0]
        == 0
    )


def test_no_keys_is_a_state_not_a_failure(seeded, archive):
    """An operator without B2 keys must not dead-letter the nightly job."""
    result = sync_raw(seeded, Settings(b2_key_id="", b2_application_key="", b2_bucket=""))
    assert result["skipped"] == "b2 not configured"
    assert result["pending"] == 3


def test_progress_is_reportable(seeded, archive):
    before = pending(seeded)
    assert before["pending"] == 3 and before["offsite"] == 0
    sync_raw(seeded, _settings(archive), client=_fake_b2([]))
    after = pending(seeded)
    assert after["offsite"] == 3 and after["offsite_bytes"] > 0
    assert utcnow()  # sanity: timestamps available for the uploaded_at column
