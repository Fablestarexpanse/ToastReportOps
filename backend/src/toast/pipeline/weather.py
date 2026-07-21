"""Weather at an incident, from the National Weather Service.

api.weather.gov is free, keyless and public-domain — the whole fire-weather
picture without another credential. For a point we resolve the forecast grid,
find the nearest reporting station (often a RAWS site), read its latest
observation, and pull any active NWS alerts (Red Flag Warnings among them).

Honesty rules:
- Values are reported with the station that produced them, its distance, and
  the observation time. Weather 30 km away on the far side of a ridge is not
  weather at the fire, and the operator must be able to see that.
- Nothing is inferred. A field the station did not report is null, not a
  guess, and fire behaviour (flame length, fuel moisture) is NOT synthesized
  here — that needs a fuels/terrain model we do not have.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger
from toast.pipeline.incidents import haversine_km

NWS = "https://api.weather.gov"
CACHE_TTL_S = 20 * 60  # observations update ~hourly; NWS asks callers not to hammer

# Alerts that change how a fire behaves or how people should act.
CRITICAL_EVENTS = {
    "Red Flag Warning",
    "Extreme Fire Danger",
    "Fire Weather Watch",
    "Excessive Heat Warning",
    "High Wind Warning",
    "Wind Advisory",
}


class WeatherError(Exception):
    pass


def _get(url: str) -> Any:
    body, _ctype = fetch(url, accept_types=("application/geo+json", "application/json"))
    return json.loads(body.decode("utf-8", "replace"))


def _c_to_f(c: float | None) -> float | None:
    return None if c is None else round(c * 9 / 5 + 32, 1)


def _kmh_to_mph(kmh: float | None) -> float | None:
    return None if kmh is None else round(kmh * 0.621371, 1)


def _value(prop: Any) -> float | None:
    if isinstance(prop, dict):
        v = prop.get("value")
        return float(v) if isinstance(v, int | float) else None
    return None


def _compass(deg: float | None) -> str | None:
    if deg is None:
        return None
    points = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    return points[int((deg / 22.5) + 0.5) % 16]


def _parse_mph(text: Any) -> float | None:
    """NWS forecast winds are strings — "10 mph", "15 to 25 mph". Take the max
    stated figure; inventing a midpoint would be our number, not theirs."""
    import re

    if not isinstance(text, str):
        return None
    nums = [float(n) for n in re.findall(r"\d+", text)]
    return max(nums) if nums else None


def _forecast_wind(point: dict[str, Any]) -> dict[str, Any] | None:
    """Next ~12 hours of wind from the NWS hourly forecast — an official product
    relayed verbatim in summary form, never modelled by us. Tonight's wind event
    is tomorrow's fire run, and the current observation cannot say so."""
    from collections import Counter

    try:
        periods = _get(point["forecastHourly"])["properties"]["periods"][:12]
    except (KeyError, TypeError, ValueError, OSError):
        return None  # a forecast failure must never lose the observation
    speeds, gusts, dirs = [], [], []
    for pd in periods:
        s = _parse_mph(pd.get("windSpeed"))
        g = _parse_mph(pd.get("windGust"))
        if s is not None:
            speeds.append(s)
        if g is not None:
            gusts.append(g)
        if pd.get("windDirection"):
            dirs.append(pd["windDirection"])
    if not speeds:
        return None
    return {
        "window_hours": len(periods),
        "peak_wind_mph": max(speeds),
        "peak_gust_mph": max(gusts) if gusts else None,
        "direction": Counter(dirs).most_common(1)[0][0] if dirs else None,
        # rising = the back half of the window is meaningfully windier than now
        "rising": speeds[-1] > speeds[0] + 3,
        "source": "NWS hourly forecast (official, relayed)",
    }


def fetch_weather(lat: float, lon: float) -> dict[str, Any]:
    """Current conditions + active alerts for a point. Raises WeatherError."""
    try:
        point = _get(f"{NWS}/points/{lat:.4f},{lon:.4f}")["properties"]
        stations = _get(point["observationStations"])["features"]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise WeatherError(f"NWS lookup failed: {exc}") from exc
    if not stations:
        raise WeatherError("no NWS reporting station covers this point")

    station = stations[0]
    sid = station["properties"]["stationIdentifier"]
    scoords = station.get("geometry", {}).get("coordinates") or [None, None]
    distance_km = (
        round(haversine_km(lat, lon, scoords[1], scoords[0]), 1) if scoords[0] is not None else None
    )

    try:
        obs = _get(f"{NWS}/stations/{sid}/observations/latest")["properties"]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise WeatherError(f"no current observation from {sid}: {exc}") from exc

    wind_kmh = _value(obs.get("windSpeed"))
    gust_kmh = _value(obs.get("windGust"))
    direction = _value(obs.get("windDirection"))

    alerts = []
    try:
        for feature in _get(f"{NWS}/alerts/active?point={lat:.4f},{lon:.4f}")["features"]:
            p = feature["properties"]
            alerts.append(
                {
                    "event": p.get("event"),
                    "severity": p.get("severity"),
                    "headline": (p.get("headline") or "")[:200],
                    "ends": p.get("ends") or p.get("expires"),
                    "critical": p.get("event") in CRITICAL_EVENTS,
                }
            )
    except (KeyError, TypeError, ValueError, OSError):
        alerts = []  # alerts are a bonus; a failure here must not lose the obs

    return {
        "station": {
            "id": sid,
            "name": station["properties"].get("name"),
            "distance_km": distance_km,
            "lat": scoords[1],
            "lon": scoords[0],
        },
        "observed_at": obs.get("timestamp"),
        "temperature_f": _c_to_f(_value(obs.get("temperature"))),
        "relative_humidity": (
            round(rh, 1) if (rh := _value(obs.get("relativeHumidity"))) is not None else None
        ),
        "wind_mph": _kmh_to_mph(wind_kmh),
        "gust_mph": _kmh_to_mph(gust_kmh),
        "wind_degrees": direction,
        "wind_compass": _compass(direction),
        "conditions": obs.get("textDescription"),
        "forecast": _forecast_wind(point),
        "alerts": alerts,
        "red_flag": any(a["event"] == "Red Flag Warning" for a in alerts),
        "critical": any(a["critical"] for a in alerts),
        "source": "NOAA/NWS api.weather.gov (public domain)",
    }


def incident_weather(
    conn: sqlite3.Connection, incident_id: str, *, refresh: bool = False
) -> dict[str, Any]:
    """Cached weather for an incident; fetches when stale or forced."""
    incident = conn.execute(
        "SELECT lat, lon, canonical_name FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if incident is None:
        raise WeatherError("incident not found")
    if incident["lat"] is None or incident["lon"] is None:
        return {"available": False, "reason": "this incident has no coordinates yet"}

    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM incident_weather
               WHERE incident_id = ?
                 AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (incident_id, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["cached"] = True
            cached["fetched_at"] = row["fetched_at"]
            return cached

    data = fetch_weather(incident["lat"], incident["lon"])
    data["available"] = True
    data["cached"] = False
    now = utcnow()
    data["fetched_at"] = now
    conn.execute(
        """INSERT INTO incident_weather (incident_id, payload, fetched_at)
           VALUES (?, ?, ?)
           ON CONFLICT(incident_id) DO UPDATE SET
             payload = excluded.payload, fetched_at = excluded.fetched_at""",
        (incident_id, json.dumps(data), now),
    )
    get_logger("weather").info(
        "weather_fetched",
        incident=incident["canonical_name"],
        station=data["station"]["id"],
        red_flag=data["red_flag"],
    )
    return data
