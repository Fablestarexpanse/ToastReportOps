"""Incident weather — conversions, caching, and refusing to invent values."""

import json

import pytest

from toast.pipeline import weather as W

NOW = "2026-07-19T12:00:00.000Z"

POINT = {
    "properties": {
        "gridId": "STO",
        "observationStations": "https://api.weather.gov/gridpoints/STO/47,121/stations",
    }
}
STATIONS = {
    "features": [
        {
            "properties": {"stationIdentifier": "JBGC1", "name": "JARBO GAP"},
            "geometry": {"coordinates": [-121.55, 39.71]},
        }
    ]
}
OBS = {
    "properties": {
        "timestamp": "2026-07-19T11:53:00+00:00",
        "temperature": {"value": 23.89, "unitCode": "wmoUnit:degC"},
        "relativeHumidity": {"value": 39.9678, "unitCode": "wmoUnit:percent"},
        "windSpeed": {"value": 6.444, "unitCode": "wmoUnit:km_h-1"},
        "windGust": {"value": 19.296, "unitCode": "wmoUnit:km_h-1"},
        "windDirection": {"value": 270, "unitCode": "wmoUnit:degree_(angle)"},
        "textDescription": "Clear",
    }
}
ALERTS = {
    "features": [
        {
            "properties": {
                "event": "Red Flag Warning",
                "severity": "Severe",
                "headline": "Red Flag Warning until 20:00",
                "ends": "2026-07-19T20:00:00-07:00",
            }
        }
    ]
}


def _routes(alerts=ALERTS, obs=OBS):
    def fake_get(url: str):
        if "/points/" in url:
            return POINT
        if url.endswith("/stations"):
            return STATIONS
        if "/observations/latest" in url:
            return obs
        if "/alerts/active" in url:
            return alerts
        raise AssertionError(f"unexpected url {url}")

    return fake_get


def test_units_are_converted_for_the_operator(monkeypatch):
    monkeypatch.setattr(W, "_get", _routes())
    data = W.fetch_weather(39.76, -121.62)
    assert data["temperature_f"] == 75.0  # 23.89C
    assert data["wind_mph"] == 4.0  # 6.444 km/h
    assert data["gust_mph"] == 12.0
    assert data["relative_humidity"] == 40.0
    assert data["wind_compass"] == "W"


def test_station_provenance_travels_with_the_reading(monkeypatch):
    """Weather 30 km away over a ridge is not weather at the fire."""
    monkeypatch.setattr(W, "_get", _routes())
    data = W.fetch_weather(39.76, -121.62)
    assert data["station"]["id"] == "JBGC1"
    assert data["station"]["distance_km"] is not None
    assert data["observed_at"].startswith("2026-07-19")
    assert "public domain" in data["source"]


def test_red_flag_is_surfaced(monkeypatch):
    monkeypatch.setattr(W, "_get", _routes())
    data = W.fetch_weather(39.76, -121.62)
    assert data["red_flag"] is True and data["critical"] is True
    assert data["alerts"][0]["event"] == "Red Flag Warning"


def test_no_alerts_is_not_a_red_flag(monkeypatch):
    monkeypatch.setattr(W, "_get", _routes(alerts={"features": []}))
    data = W.fetch_weather(39.76, -121.62)
    assert data["red_flag"] is False and data["alerts"] == []


def test_missing_fields_stay_null_rather_than_guessed(monkeypatch):
    sparse = {
        "properties": {
            "timestamp": "2026-07-19T11:53:00+00:00",
            "temperature": {"value": None},
            "windSpeed": {"value": None},
        }
    }
    monkeypatch.setattr(W, "_get", _routes(obs=sparse))
    data = W.fetch_weather(39.76, -121.62)
    assert data["temperature_f"] is None
    assert data["wind_mph"] is None
    assert data["relative_humidity"] is None


def test_incident_without_coordinates_reports_unavailable(seeded):
    result = W.incident_weather(seeded, "i1")
    assert result["available"] is False
    assert "coordinates" in result["reason"]


def test_reading_is_cached_then_reused(seeded, monkeypatch):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return _routes()(url)

    monkeypatch.setattr(W, "_get", counting)
    first = W.incident_weather(seeded, "i1")
    assert first["cached"] is False
    after_first = calls["n"]

    second = W.incident_weather(seeded, "i1")
    assert second["cached"] is True
    assert calls["n"] == after_first  # nothing refetched
    assert (
        json.loads(
            seeded.execute(
                "SELECT payload FROM incident_weather WHERE incident_id='i1'"
            ).fetchone()[0]
        )["station"]["id"]
        == "JBGC1"
    )


def test_refresh_forces_a_fetch(seeded, monkeypatch):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return _routes()(url)

    monkeypatch.setattr(W, "_get", counting)
    W.incident_weather(seeded, "i1")
    before = calls["n"]
    W.incident_weather(seeded, "i1", refresh=True)
    assert calls["n"] > before


def test_upstream_failure_raises_rather_than_returning_fiction(monkeypatch):
    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr(W, "_get", boom)
    with pytest.raises(W.WeatherError):
        W.fetch_weather(39.76, -121.62)
