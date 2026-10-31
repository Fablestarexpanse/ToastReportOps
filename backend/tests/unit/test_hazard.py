from datetime import date

import pytest

from toast.core.appsettings import set_setting
from toast.core.hazard import hazard_profile, includes_fire, includes_flood, season_suggestion


def test_defaults_to_fire(db):
    assert hazard_profile(db) == "fire"
    assert includes_fire(db) and not includes_flood(db)


@pytest.mark.parametrize(
    "value,fire,flood", [("fire", True, False), ("flood", False, True), ("both", True, True)]
)
def test_profiles(db, value, fire, flood):
    set_setting(db, "hazard_profile", value)
    assert (includes_fire(db), includes_flood(db)) == (fire, flood)


def test_rejects_unknown_profile(db):
    with pytest.raises(ValueError):
        set_setting(db, "hazard_profile", "tsunami")


def test_garbage_in_the_table_falls_back_to_fire(db):
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('hazard_profile','x','t')"
    )
    assert hazard_profile(db) == "fire"


def test_season_suggestion():
    assert season_suggestion(date(2026, 8, 1)) == "fire"
    assert season_suggestion(date(2026, 1, 15)) == "flood"
    assert season_suggestion(date(2026, 10, 31)) == "both"
