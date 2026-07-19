"""NWS CAP active alerts for California (T1 official).

api.weather.gov GeoJSON. Fire-relevant events only by default; alert text is
stored verbatim in the payload (hostile input — never interpolated anywhere).
"""

from __future__ import annotations

import json
from typing import Any

from toast.core.envelope import Envelope
from toast.sources import register
from toast.sources.capture import RawCapture

DEFAULT_URL = "https://api.weather.gov/alerts/active?area=CA"

FIRE_EVENTS = {
    "Red Flag Warning",
    "Fire Weather Watch",
    "Fire Warning",
    "Evacuation - Immediate",
    "Extreme Fire Danger",
}


@register("nws_cap")
class NwsCapAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        url = config.get("url", DEFAULT_URL)
        events = set(config.get("events", FIRE_EVENTS))
        body, _ctype = self.fetch_captured(url)
        data = json.loads(body)

        envelopes: list[Envelope] = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            if events and props.get("event") not in events:
                continue
            envelopes.append(
                Envelope(
                    source_id=config["source_id"],
                    external_id=props.get("id") or feature.get("id"),
                    kind="alert",
                    payload={
                        "event": props.get("event"),
                        "headline": props.get("headline"),
                        "severity": props.get("severity"),
                        "urgency": props.get("urgency"),
                        "area_desc": props.get("areaDesc"),
                        "description": props.get("description"),
                        "onset": props.get("onset"),
                        "sent": props.get("sent"),
                        "effective": props.get("effective"),
                        "expires": props.get("expires"),
                    },
                    observed_hint=props.get("sent") or props.get("effective"),
                    url=feature.get("id"),
                )
            )
        return envelopes, cursor  # active-alerts endpoint is a snapshot; dedup handles repeats
