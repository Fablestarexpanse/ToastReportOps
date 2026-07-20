"""Credential store — the guarantees that matter are about NOT leaking."""

import gzip
import sqlite3

import httpx
import pytest

from toast.backup import create_snapshot
from toast.core.config import Settings
from toast.core.credcheck import run_check
from toast.core.credentials import (
    ALL_FIELDS,
    SERVICES,
    get_credential,
    mask,
    service_status,
    set_credential,
    settings_with_credentials,
)

SECRET = "bskyapp-super-secret-value-9f3a"


def test_secret_value_is_never_returned_by_the_api_surface(seeded):
    set_credential(seeded, "BLUESKY_APP_PASSWORD", SECRET)
    blob = str(service_status(seeded))
    assert SECRET not in blob  # the whole payload, not just one field
    field = next(
        f
        for s in service_status(seeded)
        if s["id"] == "bluesky"
        for f in s["fields"]
        if f["name"] == "BLUESKY_APP_PASSWORD"
    )
    assert field["set"] is True
    assert field["masked"].endswith("9f3a")  # enough to recognise
    assert SECRET[:10] not in field["masked"]  # not enough to use


def test_public_identifiers_are_shown_in_full(seeded):
    set_credential(seeded, "BLUESKY_HANDLE", "toast.bsky.social")
    field = next(
        f
        for s in service_status(seeded)
        if s["id"] == "bluesky"
        for f in s["fields"]
        if f["name"] == "BLUESKY_HANDLE"
    )
    assert field["masked"] == "toast.bsky.social"  # a handle is not a secret


def test_mask_never_reveals_a_short_secret():
    assert mask("abc") == "•••"
    assert "abc" not in mask("abc")


def test_only_allowlisted_names_are_accepted(seeded):
    with pytest.raises(ValueError, match="unknown credential"):
        set_credential(seeded, "TOAST_DB_PATH", "/etc/passwd")
    with pytest.raises(ValueError, match="unknown credential"):
        set_credential(seeded, "LLM_BASE_URL", "http://evil.example")


def test_clearing_a_credential_falls_back_to_env(seeded, monkeypatch):
    monkeypatch.setenv("AIRNOW_API_KEY", "from-dotenv")
    set_credential(seeded, "AIRNOW_API_KEY", "from-operator")
    assert get_credential(seeded, "AIRNOW_API_KEY") == "from-operator"
    set_credential(seeded, "AIRNOW_API_KEY", "")
    assert get_credential(seeded, "AIRNOW_API_KEY") == "from-dotenv"


def test_stored_credentials_reach_consumers(seeded):
    set_credential(seeded, "B2_KEY_ID", "kid")
    set_credential(seeded, "B2_APPLICATION_KEY", "kval")
    set_credential(seeded, "B2_BUCKET", "toast-bucket")
    s = settings_with_credentials(seeded)
    assert (s.b2_key_id, s.b2_application_key, s.b2_bucket) == ("kid", "kval", "toast-bucket")


def test_every_registry_field_maps_to_a_settings_attribute():
    """A field with no Settings attribute would be stored and silently ignored."""
    base = Settings(_env_file=None)
    for name in ALL_FIELDS:
        assert hasattr(base, name.lower()), f"{name} has no Settings attribute"


def test_every_service_has_a_signup_link_and_a_check():
    from toast.core.credcheck import CHECKS

    for s in SERVICES:
        assert s.signup_url.startswith("https://"), s.id
        assert s.signup_label
        assert s.id in CHECKS, f"{s.id} has no connection test"


# ---- the critical one: secrets must not leave the machine in a backup ----


def test_snapshots_do_not_contain_credentials(seeded, tmp_path):
    set_credential(seeded, "BLUESKY_APP_PASSWORD", SECRET)
    manifest = create_snapshot(
        seeded, Settings(_env_file=None, toast_data_dir=tmp_path), compress=False
    )
    snap = tmp_path / "backups" / manifest["file"]

    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
    conn.close()
    # and not recoverable from free pages either
    assert SECRET.encode() not in snap.read_bytes()


def test_compressed_snapshots_do_not_contain_credentials(seeded, tmp_path):
    set_credential(seeded, "B2_APPLICATION_KEY", SECRET)
    manifest = create_snapshot(seeded, Settings(_env_file=None, toast_data_dir=tmp_path))
    snap = tmp_path / "backups" / manifest["file"]
    with gzip.open(snap, "rb") as fh:
        assert SECRET.encode() not in fh.read()


# ---- connection tests ----


def _client(responder):
    return httpx.MockTransport(responder)


def test_check_reports_unconfigured_without_a_key(seeded):
    result = run_check(seeded, "firms")
    assert result["status"] == "unconfigured"


def test_check_distinguishes_rejected_from_error(seeded):
    set_credential(seeded, "AIRNOW_API_KEY", "bad-key")

    rejecting = httpx.Client(transport=_client(lambda r: httpx.Response(401, text="unauthorized")))
    assert run_check(seeded, "airnow", client=rejecting)["status"] == "rejected"

    def exploding(request):
        raise httpx.ConnectError("dns failure")

    result = run_check(seeded, "airnow", client=httpx.Client(transport=_client(exploding)))
    assert result["status"] == "error"  # provider unreachable ≠ bad credentials


def test_successful_check_is_persisted(seeded):
    set_credential(seeded, "AIRNOW_API_KEY", "good-key")
    ok = httpx.Client(transport=_client(lambda r: httpx.Response(200, json=[])))
    assert run_check(seeded, "airnow", client=ok)["status"] == "ok"
    row = seeded.execute("SELECT status FROM credential_checks WHERE service='airnow'").fetchone()
    assert row["status"] == "ok"
    assert service_status(seeded)[-1]["status"] == "ok"  # airnow is last in the registry


def test_clearing_credentials_resets_a_stale_verdict(seeded):
    """A REJECTED badge must not linger over a service whose key was removed."""
    set_credential(seeded, "AIRNOW_API_KEY", "bad-key")
    run_check(
        seeded,
        "airnow",
        client=httpx.Client(transport=_client(lambda r: httpx.Response(401))),
    )
    assert next(s for s in service_status(seeded) if s["id"] == "airnow")["status"] == "rejected"

    set_credential(seeded, "AIRNOW_API_KEY", "")
    svc = next(s for s in service_status(seeded) if s["id"] == "airnow")
    assert svc["status"] == "unconfigured"
    assert svc["message"] is None


def test_unknown_service_rejected(seeded):
    with pytest.raises(ValueError):
        run_check(seeded, "not-a-service")
