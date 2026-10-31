"""The verifier must catch the mechanical failures the prompt only asks the model to avoid."""

import json

import pytest

from tests.unit.test_drafting import EVAC_TEXT, NOW, FakeLLM, _w
from toast.pipeline.drafting import generate_draft
from toast.pipeline.verify_draft import summarize, verify_draft


def _codes(issues):
    return sorted({i["code"] for i in issues})


@pytest.fixture()
def facts(seeded):
    _w(seeded, "f1", "acreage", "Fire at 1200 acres", NOW, None, None)
    _w(seeded, "f2", "containment", "0% contained", NOW, None, None)
    _w(seeded, "f3", "evac", EVAC_TEXT, NOW, None, "order")
    rows = seeded.execute("SELECT * FROM facts ORDER BY id").fetchall()
    return rows, {str(i): r["id"] for i, r in enumerate(rows, start=1)}


GOOD = f"The fire is 1,200 acres [#1] and 0% contained [#2]. “{EVAC_TEXT}” [#3]"


def test_clean_draft_has_no_issues(facts):
    rows, id_map = facts
    assert verify_draft(GOOD, rows, id_map) == []


def test_invented_number_is_flagged(facts):
    rows, id_map = facts
    body = GOOD.replace("1,200", "1,500")
    assert "number_not_in_facts" in _codes(verify_draft(body, rows, id_map))


def test_rounded_number_is_flagged(facts):
    rows, id_map = facts
    body = GOOD.replace("1,200 acres", "about 1,000 acres")
    assert "number_not_in_facts" in _codes(verify_draft(body, rows, id_map))


def test_paraphrased_evacuation_is_flagged(facts):
    rows, id_map = facts
    body = "The fire is 1,200 acres [#1]. Residents should leave the area [#3]."
    assert "evac_not_verbatim" in _codes(verify_draft(body, rows, id_map))


def test_omitted_evacuation_is_flagged(facts):
    rows, id_map = facts
    body = "The fire is 1,200 acres [#1] and 0% contained [#2]."
    assert "evac_not_verbatim" in _codes(verify_draft(body, rows, id_map))


def test_unknown_citation_is_flagged(facts):
    rows, id_map = facts
    assert "unknown_citation" in _codes(verify_draft(GOOD + " Winds are light [#9].", rows, id_map))


def test_uncited_figure_is_flagged(facts):
    rows, id_map = facts
    assert "uncited_figure" in _codes(verify_draft(GOOD + "\nIt is 0% contained.", rows, id_map))


def test_times_and_dates_are_not_mistaken_for_figures(facts):
    rows, id_map = facts
    body = GOOD + " Updated 5:39 PM on Jul 18."
    assert "number_not_in_facts" not in _codes(verify_draft(body, rows, id_map))


def test_sources_section_cannot_satisfy_the_evac_check(facts):
    rows, id_map = facts
    body = "The fire is 1,200 acres [#1].\n---\n\n**Sources**\n\n1. " + EVAC_TEXT
    assert "evac_not_verbatim" in _codes(verify_draft(body, rows, id_map))


def test_summarize():
    assert summarize([]) == ""
    assert "2 issue(s)" in summarize([{"code": "a"}, {"code": "b"}])


def test_generate_draft_stores_the_verdict(seeded):
    _w(seeded, "f1", "acreage", "Fire at 1200 acres", NOW, None, None)
    bad = FakeLLM(
        {"title": "T", "body_md": "The fire is 9,999 acres [#1].", "cited_fact_ids": ["1"]}
    )
    out = generate_draft(seeded, "i1", client=bad)
    row = seeded.execute(
        "SELECT verify_issues FROM drafts WHERE id = ?", (out["draft_id"],)
    ).fetchone()
    assert "number_not_in_facts" in {i["code"] for i in json.loads(row["verify_issues"])}
    assert out["verify_issues"]
