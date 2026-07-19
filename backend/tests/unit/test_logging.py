from toast.core.logging import REDACTED, scrub_secrets


def test_secret_keys_redacted():
    event = {
        "event": "publish",
        "x_api_key": "abc123",
        "session_token": "tok",
        "password": "hunter2",
        "b2_application_key": "k",
        "channel": "x",
    }
    out = scrub_secrets(None, "info", event)
    assert out["x_api_key"] == REDACTED
    assert out["session_token"] == REDACTED
    assert out["password"] == REDACTED
    assert out["b2_application_key"] == REDACTED
    assert out["channel"] == "x"  # non-secret keys untouched
    assert out["event"] == "publish"
