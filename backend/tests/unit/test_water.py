"""Flood gauges + alerts: relay official categories, surface only what matters."""

import json

import httpx

from toast.core.appsettings import set_setting
from toast.core.config import Settings
from toast.pipeline import water

SET = Settings(telegram_bot_token="123:abc", telegram_ops_chat_id="777")


def gauge(lid, obs="no_flooding", fcst="no_flooding", valid="2099-01-01T00:00:00Z", name=None):
    return {
        "lid": lid,
        "name": name or lid,
        "latitude": 39.7,
        "longitude": -121.7,
        "status": {
            "observed": {
                "primary": 12.5,
                "primaryUnit": "ft",
                "secondary": 4.2,
                "secondaryUnit": "kcfs",
                "floodCategory": obs,
                "validTime": valid,
            },
            "forecast": {"primary": 13.0, "primaryUnit": "ft", "floodCategory": fcst},
        },
    }


def alert(event, id_="a1", severity="Severe"):
    return {
        "properties": {
            "id": id_,
            "event": event,
            "severity": severity,
            "areaDesc": "Butte County",
            "description": "Heavy rain.",
            "instruction": "Move to higher ground.",
        }
    }


def test_only_flooding_or_pinned_gauges_are_shown():
    raw = [gauge("CALM1"), gauge("MIN1", obs="minor"), gauge("FC1", fcst="action"), gauge("PIN1")]
    shown = water.build_gauges(raw, {"PIN1"})
    assert [g["lid"] for g in shown] == ["MIN1", "FC1", "PIN1"]  # severity, then pinned


def test_worst_of_observed_and_forecast_sorts_first():
    raw = [gauge("A", obs="action"), gauge("B", fcst="major"), gauge("C", obs="moderate")]
    assert [g["lid"] for g in water.build_gauges(raw, set())] == ["B", "C", "A"]


def test_old_observation_is_marked_stale():
    old = gauge("A", obs="minor", valid="2020-01-01T00:00:00Z")
    assert water.build_gauges([old], set())[0]["stale"] is True
    assert water.build_gauges([gauge("B", obs="minor")], set())[0]["stale"] is False


def test_undefined_category_is_not_flooding():
    assert water.build_gauges([gauge("A", obs="not_defined")], set()) == []


def test_alerts_keep_only_flood_events_most_severe_first():
    feats = [
        alert("Red Flag Warning", "x"),
        alert("Flood Advisory", "b", "Minor"),
        alert("Flash Flood Warning", "a", "Extreme"),
    ]
    out = water.build_alerts(feats)
    assert [a["id"] for a in out] == ["a", "b"]
    assert out[0]["instruction"] == "Move to higher ground."  # verbatim


def test_pinned_gauges_come_from_the_setting(db):
    set_setting(db, "water_watch_lids", "bkcc1; xyz1 ,")
    assert water.watched_lids(db) == {"BKCC1", "XYZ1"}


def _feeds(monkeypatch, gauges=None, features=None, fail=()):
    def fake(url):
        if "gauges" in url and "gauges" in fail:
            raise OSError("down")
        if "alerts" in url and "alerts" in fail:
            raise OSError("down")
        return {"gauges": gauges or []} if "gauges" in url else {"features": features or []}

    monkeypatch.setattr(water, "_get_json", fake)


def test_one_feed_down_does_not_blank_the_other(db, monkeypatch):
    _feeds(monkeypatch, gauges=[gauge("A", obs="minor")], fail=("alerts",))
    out = water.get_water(db, refresh=True)
    assert [g["lid"] for g in out["gauges"]] == ["A"] and out["errors"] == ["alerts: OSError"]


def test_both_feeds_down_raises_rather_than_caching_all_clear(db, monkeypatch):
    _feeds(monkeypatch, fail=("gauges", "alerts"))
    try:
        water.get_water(db, refresh=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert (
        db.execute("SELECT 1 FROM map_cache WHERE key = ?", (water.CACHE_KEY,)).fetchone() is None
    )


def test_cache_serves_second_call_and_applies_new_pins(db, monkeypatch):
    _feeds(monkeypatch, gauges=[gauge("CALM1")])
    assert water.get_water(db, refresh=True)["gauges"] == []
    monkeypatch.setattr(water, "_get_json", lambda u: (_ for _ in ()).throw(AssertionError("hit")))
    set_setting(db, "water_watch_lids", "CALM1")
    out = water.get_water(db)
    assert out["cached"] and [g["lid"] for g in out["gauges"]] == ["CALM1"]


def _recorder():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def test_notifies_new_urgent_warning_once_after_baseline(db):
    sent, client = _recorder()
    quiet = {"alerts": [], "gauges": []}
    assert water.notify_water(db, quiet, SET, client)["baselined"]

    warn = {"alerts": water.build_alerts([alert("Flash Flood Warning")]), "gauges": []}
    assert water.notify_water(db, warn, SET, client)["sent"] == 1
    assert water.notify_water(db, warn, SET, client)["sent"] == 0  # same alert, once
    assert "FLASH FLOOD WARNING" in sent[0]


def test_advisories_do_not_wake_the_phone(db):
    sent, client = _recorder()
    water.notify_water(db, {"alerts": [], "gauges": []}, SET, client)
    adv = {"alerts": water.build_alerts([alert("Flood Advisory", "z", "Minor")]), "gauges": []}
    assert water.notify_water(db, adv, SET, client)["sent"] == 0


def test_gauge_reaching_moderate_alerts_once(db):
    sent, client = _recorder()
    water.notify_water(db, {"alerts": [], "gauges": []}, SET, client)
    g = {
        "alerts": [],
        "gauges": water.build_gauges([gauge("A", obs="moderate", name="Big Creek")], set()),
    }
    assert water.notify_water(db, g, SET, client)["sent"] == 1
    assert water.notify_water(db, g, SET, client)["sent"] == 0
    assert "Big Creek" in sent[0]


def test_noaa_zero_time_means_no_reading_time():
    """Go's zero time, sent on 138 observed and 702 forecast readings at once."""
    g = gauge("A", obs="action", valid="0001-01-01T00:00:00Z")
    (shown,) = water.build_gauges([g], set())
    assert shown["observed"]["valid_time"] is None
    assert shown["stale"] is True


def test_flow_primary_gauge_keeps_stage_and_flow_the_right_way_round():
    """Some gauges report flow first and stage second. Flow was dropped and the
    stage, in feet, was shown as the flow."""
    g = gauge("A", obs="action")
    g["status"]["observed"].update(
        {"primary": 0.282, "primaryUnit": "kcfs", "secondary": 2.8, "secondaryUnit": "ft"}
    )
    (shown,) = water.build_gauges([g], set())
    assert shown["observed"]["stage"] == 2.8
    assert shown["observed"]["flow"] == 0.282
    assert shown["observed"]["flow_unit"] == "kcfs"


def test_negative_nine_point_nine_nine_is_no_reading_but_a_real_negative_stage_is_kept():
    g = gauge("A", obs="action")
    g["status"]["observed"]["primary"] = -9.99
    (shown,) = water.build_gauges([g], set())
    assert shown["observed"]["stage"] is None

    tidal = gauge("B", obs="action")
    tidal["status"]["observed"]["primary"] = -1.54
    (kept,) = water.build_gauges([tidal], set())
    assert kept["observed"]["stage"] == -1.54


def test_noaa_no_reading_sentinels_become_unknown_not_numbers():
    g = gauge("A", obs="action")
    g["status"]["observed"]["secondary"] = -999
    g["status"]["observed"]["primary"] = -9999
    (shown,) = water.build_gauges([g], set())
    assert shown["observed"]["flow"] is None and shown["observed"]["stage"] is None
    ok = gauge("B", obs="action")
    (fine,) = water.build_gauges([ok], set())
    assert fine["observed"]["stage"] == 12.5 and fine["observed"]["flow"] == 4.2
