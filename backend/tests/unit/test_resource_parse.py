"""Equipment counts read from reported text — wrong counts are worse than none."""

from toast.pipeline.resource_parse import parse_counts, reported_resources


def test_reads_digits_and_number_words():
    counts = parse_counts("23 engines and two helicopters are working the fire today")
    assert counts["engines"] == 23
    assert counts["aircraft"] == 2


def test_reads_personnel_with_thousands_separator():
    assert parse_counts("1,204 personnel are assigned to the incident")["personnel"] == 1204


def test_reads_crews_and_dozers():
    counts = parse_counts("Six hand crews and 3 dozers remain committed overnight")
    assert counts["crews"] == 6 and counts["dozers"] == 3


def test_policy_sentence_is_not_a_deployment_count():
    """'Increasing staffing from two to three firefighters' is an argument
    about policy, not three firefighters on this fire."""
    assert (
        parse_counts("Increasing engine staffing from two to three firefighters would help") == {}
    )


def test_sentence_without_a_deployment_cue_is_ignored():
    assert parse_counts("The county has 40 engines in its fleet") == {}


def test_no_numbers_yields_nothing():
    assert parse_counts("Crews and engines continue to secure the line") == {}


def test_water_tenders_are_not_counted_as_aircraft():
    counts = parse_counts("Four water tenders are working the south flank")
    assert counts.get("tenders") == 4
    assert "aircraft" not in counts


def test_air_tankers_count_as_aircraft():
    assert parse_counts("Two air tankers were assigned this afternoon")["aircraft"] == 2


def test_absurd_values_are_rejected():
    assert parse_counts("999999 engines are assigned") == {}


def test_newest_report_wins_per_resource_type(seeded=None):
    rows = [
        {
            "content": "12 engines are assigned",
            "observed_at": "2026-07-20T12:00:00Z",
            "source": "KQED",
            "tier": 4,
        },
        {
            "content": "8 engines are assigned and 2 dozers working",
            "observed_at": "2026-07-19T12:00:00Z",
            "source": "ABC10",
            "tier": 4,
        },
    ]
    out = reported_resources(rows)
    assert out["engines"]["value"] == 12  # newer wins
    assert out["dozers"]["value"] == 2  # older still supplies what the newer lacks
    assert out["engines"]["source"] == "KQED"


def test_every_count_keeps_the_sentence_it_came_from():
    """A parsed number must be auditable at a glance."""
    rows = [
        {
            "content": "23 engines are working the fire",
            "observed_at": "x",
            "source": "KQED",
            "tier": 4,
        }
    ]
    out = reported_resources(rows)
    assert "23 engines" in out["engines"]["quote"]
