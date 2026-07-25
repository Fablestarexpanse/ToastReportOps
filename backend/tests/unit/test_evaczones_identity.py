"""Evacuation zone IDENTITY and hazard tagging.

A zone's id is not decoration: a resident reads "YUB-E098-A is under a warning"
and decides whether to leave. The map used to show only the last dash segment,
which rendered ICU-123-A and ICU-143-A both as "A" — two different zones under
opposite instructions, wearing the same label. These tests hold that line.
"""

from toast.pipeline.evaczones import (
    _parent_zone,
    hazard_of,
    public_zone_id,
)


class TestPublicZoneId:
    def test_keeps_full_identity_not_just_the_last_segment(self):
        # the county's own notice calls this zone "YUB-E098-A", verbatim
        assert public_zone_id("US-CA-XYU-YUB-E098-A") == "YUB-E098-A"
        assert public_zone_id("US-CA-XYU-YUB-E098-B") == "YUB-E098-B"

    def test_zones_that_used_to_collide_are_now_distinct(self):
        # the regression that started this: both rendered as "A"
        a = public_zone_id("US-CA-XIN-ICU-123-A")
        b = public_zone_id("US-CA-XIN-ICU-143-A")
        assert a == "ICU-123-A"
        assert b == "ICU-143-A"
        assert a != b

    def test_zone_without_a_sub_letter(self):
        assert public_zone_id("US-CA-XTU-PVL-E044") == "PVL-E044"
        assert public_zone_id("US-CA-XLS-LAS-553") == "LAS-553"

    def test_unknown_shapes_pass_through_untouched(self):
        # never invent or mangle an id we do not recognise
        assert public_zone_id("PARADISE-A") == "PARADISE-A"
        assert public_zone_id("") is None
        assert public_zone_id(None) is None


class TestHazard:
    def test_wildfire_notes_read_as_fire(self):
        assert hazard_of({"NOTES": "Evacuation ORDER in place for an active wildfire."}) == "fire"
        assert hazard_of({"NOTES": "Evacuation WARNING for an active wild fire."}) == "fire"

    def test_non_fire_evacuations_are_named(self):
        # Tulare's flood evacuations ride the same statewide layer; a wildfire
        # tool must not present them as fire orders
        assert hazard_of({"NOTES": "Flooding"}) == "flood"
        assert hazard_of({"NOTES": "Debris flow risk in burn scar"}) == "debris flow"

    def test_silent_zone_claims_nothing(self):
        assert hazard_of({}) is None
        assert hazard_of({"NOTES": "", "PUBLIC_INFO": None}) is None


class TestParentZone:
    def test_split_siblings_share_a_parent(self):
        assert _parent_zone("US-CA-XIN-ICU-123-A") == "ICU-123"
        assert _parent_zone("US-CA-XIN-ICU-123-D") == "ICU-123"

    def test_unsplit_zone_is_its_own_parent(self):
        assert _parent_zone("US-CA-XIN-ICU-127") == "ICU-127"
