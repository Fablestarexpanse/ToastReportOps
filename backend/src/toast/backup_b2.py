"""Backblaze B2 upload for snapshots — the copy that leaves the property.

Uses B2's native API over httpx (no extra dependency). Credential-gated: with
no keys in .env this module is never imported. 10 GB free tier covers years of
compressed ledger snapshots.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.logging import get_logger

AUTH_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


class BackupUploadError(Exception):
    pass


def _authorize(s: Settings, client: httpx.Client) -> dict[str, Any]:
    r = client.get(AUTH_URL, auth=(s.b2_key_id, s.b2_application_key))
    if r.status_code != 200:
        raise BackupUploadError(f"b2 authorize failed: HTTP {r.status_code}")
    return r.json()


def _bucket_id(auth: dict[str, Any], s: Settings, client: httpx.Client) -> str:
    api = auth["apiInfo"]["storageApi"]
    r = client.post(
        f"{api['apiUrl']}/b2api/v3/b2_list_buckets",
        headers={"Authorization": auth["authorizationToken"]},
        json={"accountId": auth["accountId"], "bucketName": s.b2_bucket},
    )
    if r.status_code != 200:
        raise BackupUploadError(f"b2 list_buckets failed: HTTP {r.status_code}")
    buckets = r.json().get("buckets", [])
    if not buckets:
        raise BackupUploadError(f"bucket {s.b2_bucket!r} not found")
    return str(buckets[0]["bucketId"])


def upload_snapshot(
    path: Path, settings: Settings | None = None, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Upload one snapshot file to B2. Returns the receipt."""
    s = settings or Settings()
    if not (s.b2_key_id and s.b2_application_key and s.b2_bucket):
        raise BackupUploadError("B2 credentials not configured")
    if not path.exists():
        raise BackupUploadError(f"snapshot missing: {path}")

    own = client is None
    client = client or httpx.Client(timeout=300)
    try:
        auth = _authorize(s, client)
        bucket_id = _bucket_id(auth, s, client)
        api = auth["apiInfo"]["storageApi"]

        r = client.post(
            f"{api['apiUrl']}/b2api/v3/b2_get_upload_url",
            headers={"Authorization": auth["authorizationToken"]},
            json={"bucketId": bucket_id},
        )
        if r.status_code != 200:
            raise BackupUploadError(f"b2 get_upload_url failed: HTTP {r.status_code}")
        up = r.json()

        data = path.read_bytes()
        r = client.post(
            up["uploadUrl"],
            headers={
                "Authorization": up["authorizationToken"],
                "X-Bz-File-Name": f"toast/{path.name}",
                "Content-Type": "application/octet-stream",
                "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),  # noqa: S324 - B2 API requires SHA1
            },
            content=data,
        )
        if r.status_code != 200:
            raise BackupUploadError(f"b2 upload failed: HTTP {r.status_code} {r.text[:120]}")
        receipt = r.json()
    finally:
        if own:
            client.close()

    get_logger("backup").info("snapshot_uploaded", file=path.name, bytes=len(data))
    return {"file_id": receipt.get("fileId"), "name": receipt.get("fileName"), "bytes": len(data)}
