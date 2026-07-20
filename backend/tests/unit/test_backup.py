import gzip
import json
import sqlite3

import httpx
import pytest

from toast.backup import (
    backup_status,
    create_snapshot,
    list_snapshots,
    prune_snapshots,
    run_backup,
    verify_snapshot,
)
from toast.backup_b2 import BackupUploadError, upload_snapshot
from toast.core.config import Settings


def _settings(tmp_path, **over) -> Settings:
    fields = {
        "toast_data_dir": tmp_path,
        "b2_key_id": "",
        "b2_application_key": "",
        "b2_bucket": "",
    }
    fields.update(over)
    return Settings(_env_file=None, **fields)


def test_snapshot_is_created_verified_and_manifested(seeded, tmp_path):
    s = _settings(tmp_path)
    manifest = create_snapshot(seeded, s)

    snap = tmp_path / "backups" / manifest["file"]
    assert snap.exists()
    assert manifest["verified"]["ok"] is True
    assert manifest["verified"]["integrity"] == "ok"
    # the manifest records what a restore must reproduce
    assert manifest["verified"]["counts"]["incidents"] == 1
    assert manifest["sha256"] and manifest["bytes"] > 0
    assert json.loads((tmp_path / "backups" / f"{manifest['file']}.json").read_text())


def test_snapshot_restores_to_a_working_database(seeded, tmp_path):
    """The drill in miniature: decompress, open, and read the ledger back."""
    seeded.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i2', 'RESTORE TEST FIRE', 'active', '2026-07-20T00:00:00Z',"
        " '2026-07-20T00:00:00Z')"
    )
    manifest = create_snapshot(seeded, _settings(tmp_path))

    restored = tmp_path / "restored.db"
    with gzip.open(tmp_path / "backups" / manifest["file"], "rb") as src:
        restored.write_bytes(src.read())

    conn = sqlite3.connect(restored)
    conn.row_factory = sqlite3.Row
    names = {r["canonical_name"] for r in conn.execute("SELECT canonical_name FROM incidents")}
    assert "RESTORE TEST FIRE" in names
    assert verify_snapshot(restored)["ok"] is True
    conn.close()


def test_uncompressed_snapshot_verifies(seeded, tmp_path):
    manifest = create_snapshot(seeded, _settings(tmp_path), compress=False)
    assert manifest["compressed"] is False
    assert verify_snapshot(tmp_path / "backups" / manifest["file"])["ok"] is True


def test_prune_keeps_newest(seeded, tmp_path):
    s = _settings(tmp_path)
    out = tmp_path / "backups"
    out.mkdir(parents=True, exist_ok=True)
    for stamp in ("20260701T000000", "20260702T000000", "20260703T000000"):
        (out / f"toast-{stamp}.db.gz").write_bytes(b"x")
        (out / f"toast-{stamp}.db.gz.json").write_text("{}")
    removed = prune_snapshots(s, keep=2)
    assert removed == ["toast-20260701T000000.db.gz"]
    remaining = {p.name for p in out.glob("toast-*.gz")}
    assert remaining == {"toast-20260702T000000.db.gz", "toast-20260703T000000.db.gz"}
    assert not (out / "toast-20260701T000000.db.gz.json").exists()


def test_status_flags_local_only_as_at_risk(seeded, tmp_path):
    s = _settings(tmp_path)
    create_snapshot(seeded, s)
    status = backup_status(s)
    assert status["snapshots"] == 1
    assert status["offsite_configured"] is False
    assert status["at_risk"] is True  # local-only is not backed up

    configured = _settings(tmp_path, b2_key_id="k", b2_application_key="v", b2_bucket="b")
    assert backup_status(configured)["at_risk"] is False


def test_run_backup_skips_offsite_without_credentials(seeded, tmp_path):
    result = run_backup(seeded, _settings(tmp_path))
    assert result["offsite"] is None
    assert len(list_snapshots(_settings(tmp_path))) == 1


def test_b2_upload_requires_credentials(tmp_path):
    f = tmp_path / "toast-x.db.gz"
    f.write_bytes(b"data")
    with pytest.raises(BackupUploadError, match="not configured"):
        upload_snapshot(f, _settings(tmp_path))


def test_b2_upload_round_trip(tmp_path):
    f = tmp_path / "backups" / "toast-20260720T000000.db.gz"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"snapshot-bytes")
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("b2_authorize_account"):
            return httpx.Response(
                200,
                json={
                    "authorizationToken": "tok",
                    "accountId": "acct",
                    "apiInfo": {"storageApi": {"apiUrl": "https://api.example"}},
                },
            )
        if path.endswith("b2_list_buckets"):
            return httpx.Response(200, json={"buckets": [{"bucketId": "bkt"}]})
        if path.endswith("b2_get_upload_url"):
            return httpx.Response(
                200, json={"uploadUrl": "https://upload.example/f", "authorizationToken": "up"}
            )
        seen["name"] = request.headers["X-Bz-File-Name"]
        seen["body"] = request.content
        return httpx.Response(200, json={"fileId": "fid", "fileName": seen["name"]})

    s = _settings(tmp_path, b2_key_id="k", b2_application_key="v", b2_bucket="toast")
    client = httpx.Client(transport=httpx.MockTransport(responder))
    receipt = upload_snapshot(f, s, client=client)
    assert receipt["file_id"] == "fid"
    assert seen["name"] == "toast/toast-20260720T000000.db.gz"
    assert seen["body"] == b"snapshot-bytes"
