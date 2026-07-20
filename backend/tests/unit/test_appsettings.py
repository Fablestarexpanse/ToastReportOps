import pytest

from toast.core.appsettings import effective_settings, get_setting, llm_overrides, set_setting
from toast.llm.client import client_for


def test_defaults_come_from_env_and_are_not_overridden(db):
    eff = effective_settings(db)
    assert eff["llm_model"]["overridden"] is False
    assert eff["timezone_display"]["value"] == "utc"
    assert eff["timeline_order"]["value"] == "newest"


def test_set_and_revert_setting(db):
    default_model = effective_settings(db)["llm_model"]["default"]
    set_setting(db, "llm_model", "llama3.1:8b")
    eff = effective_settings(db)
    assert eff["llm_model"]["value"] == "llama3.1:8b"
    assert eff["llm_model"]["overridden"] is True

    set_setting(db, "llm_model", "")  # empty reverts to the .env default
    eff = effective_settings(db)
    assert eff["llm_model"]["value"] == default_model
    assert eff["llm_model"]["overridden"] is False
    assert get_setting(db, "llm_model") is None


def test_unknown_key_rejected(db):
    with pytest.raises(ValueError):
        set_setting(db, "llm_base_url", "http://evil.example")


def test_overlong_value_rejected(db):
    with pytest.raises(ValueError):
        set_setting(db, "llm_model", "x" * 201)


def test_client_for_uses_operator_model_choice(db):
    assert llm_overrides(db) == {}
    set_setting(db, "llm_model", "qwen3:32b")
    set_setting(db, "embedding_model", "mxbai-embed-large")
    client = client_for(db)
    assert client.model == "qwen3:32b"
    assert client.embed_model == "mxbai-embed-large"
