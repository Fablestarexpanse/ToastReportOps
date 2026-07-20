"""A key must not be able to leave this machine.

The FIRMS map key lives in the URL path, so a single 401 used to carry it into
an exception string -> the worker's log line -> jobs.last_error -> the nightly
snapshot -> B2. Nothing had leaked, because no key was set yet; the failure
would have armed itself the day one was added.
"""

import sqlite3

from toast.core.fetch import redact_url
from toast.core.logging import scrub_secrets

KEY = "9f3c2a1b8e7d6c5b4a39281706f5e4d3"
FIRMS_URL = (
    f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_NRT/-124,32,-114,42/1"
)


class TestRedaction:
    def test_a_key_in_the_path_is_masked(self):
        """Shape cannot identify it — the caller declares what its secret is."""
        out = redact_url(FIRMS_URL, secrets=(KEY,))
        assert KEY not in out
        assert "firms.modaps.eosdis.nasa.gov" in out  # still diagnosable

    def test_a_key_in_a_query_parameter_is_masked_by_name(self):
        for param in ("api_key", "key", "token", "access_token", "password"):
            out = redact_url(f"https://example.com/x?{param}={KEY}&bbox=1,2")
            assert KEY not in out, param

    def test_an_ordinary_url_is_left_alone(self):
        url = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false"
        assert redact_url(url) == url

    def test_empty_secret_does_not_redact_everything(self):
        assert redact_url("https://example.com/a", secrets=("",)) == "https://example.com/a"


class TestFetchErrorsCarryNoSecret:
    def test_http_error_message_is_redacted(self, monkeypatch):
        """The exact path the audit traced: 401 -> FetchError -> log -> DB."""
        import httpx

        from toast.core import fetch as fetch_mod

        class FakeResp:
            is_redirect = False
            status_code = 401
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def stream(self, method, url):
                return FakeResp()

        monkeypatch.setattr(fetch_mod, "_assert_public_host", lambda url: None)
        monkeypatch.setattr(httpx, "Client", FakeClient)
        try:
            fetch_mod.fetch(FIRMS_URL, secrets=(KEY,))
            raise AssertionError("expected FetchError")
        except fetch_mod.FetchError as exc:
            assert KEY not in str(exc)
            assert KEY not in repr(exc)  # repr() is what the worker logs


class TestLogScrubbing:
    def test_secret_named_fields_are_still_scrubbed(self):
        out = scrub_secrets(None, "info", {"event": "x", "api_key": KEY, "source": "firms"})
        assert out["api_key"] == "[REDACTED]"
        assert out["source"] == "firms"


class TestSnapshotScrubbing:
    def test_a_key_recorded_in_jobs_last_error_is_scrubbed_from_the_snapshot(self, tmp_path):
        """Defence in depth for anything already recorded, or a future writer
        that forgets to declare its secret."""
        from toast.backup import _strip_secrets

        snap = tmp_path / "snap.db"
        conn = sqlite3.connect(snap)
        conn.execute("CREATE TABLE credentials (name TEXT, value TEXT)")
        conn.execute("CREATE TABLE jobs (id TEXT, last_error TEXT)")
        conn.execute("INSERT INTO credentials VALUES ('FIRMS_MAP_KEY', ?)", (KEY,))
        conn.execute(
            "INSERT INTO jobs VALUES ('j1', ?)", (f"FetchError('HTTP 401 from {FIRMS_URL}')",)
        )
        conn.execute("INSERT INTO jobs VALUES ('j2', 'ConnectError(unreachable)')")
        conn.commit()
        conn.close()

        _strip_secrets(snap)

        conn = sqlite3.connect(snap)
        errors = [r[0] for r in conn.execute("SELECT last_error FROM jobs")]
        assert not any(KEY in (e or "") for e in errors)
        assert any("[REDACTED]" in (e or "") for e in errors)
        assert conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
        conn.close()
