"""Operator-edited prompts — the guard is that a saved prompt still works."""

import pytest

from toast.llm.prompts import (
    EDITABLE_PROMPTS,
    clear_override,
    default_template,
    list_editable,
    load_prompt,
    placeholders,
    save_override,
)


def test_defaults_load_from_file_when_nothing_is_saved(db):
    prompt = load_prompt("draft_update", conn=db)
    assert "{facts}" in prompt.template
    assert prompt.version == "v1"


def test_saved_prompt_replaces_the_default(db):
    edited = default_template("draft_update") + "\n\nAlways mention the county.\n"
    save_override(db, "draft_update", edited)
    prompt = load_prompt("draft_update", conn=db)
    assert "Always mention the county." in prompt.template


def test_edited_prompts_are_marked_in_provenance(db):
    """A fact's prompt_version must show it came from an edited prompt."""
    save_override(db, "draft_update", default_template("draft_update") + "\nx\n")
    assert load_prompt("draft_update", conn=db).prompt_version.endswith("-edited")


def test_missing_placeholder_is_refused(db):
    """A prompt without {facts} still runs — it just never sees the ledger."""
    broken = default_template("draft_update").replace("{facts}", "the facts")
    with pytest.raises(ValueError, match="missing placeholder"):
        save_override(db, "draft_update", broken)
    # and nothing was stored
    assert "{facts}" in load_prompt("draft_update", conn=db).template


def test_too_short_is_refused(db):
    with pytest.raises(ValueError, match="too short"):
        save_override(db, "draft_update", "write something nice {facts}")


def test_non_editable_prompt_is_refused(db):
    """extract_claims is the ledger's intake contract, not a style choice."""
    with pytest.raises(ValueError, match="not operator-editable"):
        save_override(db, "extract_claims", "x" * 100)


def test_revert_restores_the_shipped_prompt(db):
    save_override(db, "rewrite_social", default_template("rewrite_social") + "\nBe funny.\n")
    assert "Be funny." in load_prompt("rewrite_social", conn=db).template
    clear_override(db, "rewrite_social")
    prompt = load_prompt("rewrite_social", conn=db)
    assert "Be funny." not in prompt.template
    assert prompt.version == "v1"  # no longer marked edited


def test_listing_reports_edit_state_and_placeholders(db):
    listed = {p["id"]: p for p in list_editable(db)}
    assert set(listed) == set(EDITABLE_PROMPTS)
    assert listed["draft_update"]["edited"] is False
    assert "facts" in listed["draft_update"]["placeholders"]

    save_override(db, "draft_update", default_template("draft_update") + "\nmore\n")
    listed = {p["id"]: p for p in list_editable(db)}
    assert listed["draft_update"]["edited"] is True
    assert listed["draft_update"]["default_template"] != listed["draft_update"]["template"]


def test_every_editable_prompt_file_exists_and_has_placeholders():
    for prompt_id, meta in EDITABLE_PROMPTS.items():
        tpl = default_template(prompt_id, meta["version"])
        assert placeholders(tpl), f"{prompt_id} has no placeholders"
