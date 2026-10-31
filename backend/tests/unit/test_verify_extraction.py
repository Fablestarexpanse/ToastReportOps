"""Extraction must stay true to its source: no invented figures, names or places."""

import pytest

from toast.llm.extract import ExtractedClaim, extract_claims
from toast.llm.verify_extraction import numbers_in, verify_claim

REF = "2026-07-19T15:00:00Z"
SRC = (
    "The Flume Fire near Paradise in Butte County has burned about 1,240 acres and is "
    "15 percent contained, Cal Fire said Sunday at 5:39 PM. Two hundred firefighters "
    "are on scene. Residents of Zone BUT-1043 were ordered to evacuate."
)


def claim(**kw):
    base = {"content": "Flume Fire at 1240 acres", "kind": "acreage", "confidence": 0.9}
    return ExtractedClaim.model_validate({**base, **kw})


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def chat_json(self, messages, **kw):
        return self.response


def test_supported_claim_is_kept_clean():
    kept, issues = verify_claim(
        claim(acreage=1240, incident_name="Flume Fire", location_text="Paradise, CA"), SRC
    )
    assert kept is not None and issues == [] and kept.incident_name == "Flume Fire"


def test_invented_acreage_drops_the_claim():
    kept, issues = verify_claim(claim(content="Flume Fire at 9999 acres", acreage=9999), SRC)
    assert kept is None and issues == ["number_not_in_source"]


def test_rounded_figure_is_not_accepted_as_stated():
    kept, _ = verify_claim(claim(content="Flume Fire at 1,200 acres", acreage=1200), SRC)
    assert kept is None  # the source says 1,240; "about 1,200" is the model's rounding


def test_number_only_in_the_acreage_field_is_checked_too():
    kept, _ = verify_claim(claim(content="Flume Fire grew", acreage=5000), SRC)
    assert kept is None


def test_invented_fire_name_is_cleared_but_the_claim_survives():
    kept, issues = verify_claim(claim(acreage=1240, incident_name="Camp Fire"), SRC)
    assert kept is not None and kept.incident_name is None and "name_not_in_source" in issues


def test_fire_name_matches_without_the_word_fire_and_ignores_case():
    kept, issues = verify_claim(claim(acreage=1240, incident_name="the flume FIRE"), SRC)
    assert issues == [] and kept is not None and kept.incident_name


def test_invented_location_is_cleared():
    kept, issues = verify_claim(claim(acreage=1240, location_text="Redding, CA"), SRC)
    assert kept is not None and kept.location_text is None and "location_not_in_source" in issues


def test_spelled_out_and_scaled_numbers_are_understood():
    assert 200 in numbers_in("Two hundred firefighters")
    assert 25 in numbers_in("twenty-five engines")
    assert 2500 in numbers_in("two thousand five hundred acres")
    assert 1200 in numbers_in("about 1.2 thousand acres")
    assert 15000 in numbers_in("15k acres")
    assert 1240 in numbers_in("1,240 acres")


def test_spelled_out_source_figure_supports_a_digit_claim():
    kept, _ = verify_claim(claim(content="200 firefighters on scene", kind="resource"), SRC)
    assert kept is not None


def test_times_and_dates_in_the_claim_are_not_treated_as_figures():
    kept, _ = verify_claim(
        claim(content="Cal Fire said at 5:39 PM on Jul 19, 2026 that 15 percent is contained",
              kind="containment", containment_pct=15),
        SRC,
    )  # fmt: skip
    assert kept is not None


def test_zero_containment_is_allowed_only_when_the_source_says_so():
    c = claim(content="Fire is uncontained", kind="containment", containment_pct=0)
    assert verify_claim(c, "The fire remains uncontained.")[0] is not None
    assert verify_claim(c, "The fire grew overnight.")[0] is None


def test_paraphrased_evacuation_is_kept_but_flagged():
    kept, issues = verify_claim(
        claim(content="Everyone near Paradise must leave", kind="evac", evac_status="order"), SRC
    )
    assert kept is not None and issues == ["evac_not_verbatim"]


def test_verbatim_evacuation_is_clean():
    kept, issues = verify_claim(
        claim(content="Residents of Zone BUT-1043 were ordered to evacuate", kind="evac"), SRC
    )
    assert kept is not None and issues == []


def test_model_cannot_pre_clear_its_own_issues():
    llm = FakeLLM(
        {"claims": [{"content": "Flume Fire", "kind": "start", "confidence": 0.9, "issues": ["x"]}]}
    )
    assert extract_claims(SRC, reference_time=REF, client=llm).claims[0].issues == []


@pytest.mark.parametrize("verify", [True, False])
def test_end_to_end_counts_unsupported_claims(verify):
    llm = FakeLLM(
        {
            "claims": [
                {"content": "Flume Fire at 1240 acres", "kind": "acreage", "acreage": 1240,
                 "confidence": 0.9},
                {"content": "Flume Fire at 80000 acres", "kind": "acreage", "acreage": 80000,
                 "confidence": 0.9},
            ]
        }
    )  # fmt: skip
    r = extract_claims(SRC, reference_time=REF, client=llm, verify=verify)
    assert (len(r.claims), r.unsupported) == ((1, 1) if verify else (2, 0))
