"""CAL FIRE incidents (T1 official) — the authoritative CA active-fire list.

incidents.fire.ca.gov IncidentApi/List. `inactive=false` (default) returns
exactly the fires CAL FIRE currently lists as active; `inactive=true` or
`year=YYYY` returns historical fires (used by the search/import flow, not the
default poller). Snapshot endpoint — no cursor; envelope dedup handles repeats.
"""

from __future__ import annotations

import json
from typing import Any

from toast.core.envelope import Envelope
from toast.sources import register
from toast.sources.capture import RawCapture

LIST_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List"
# CAL FIRE blocks the default httpx UA; a browser UA is required.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ToastReport/0.1"


def _f(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    """Normalize CAL FIRE timestamps (already ISO-8601 Z) to our format."""
    if not value:
        return None
    s = str(value)
    return s if s.endswith("Z") else s + "Z"


def incident_to_payload(item: dict[str, Any], source_id: str) -> Envelope:
    is_active = bool(item.get("IsActive"))
    return Envelope(
        source_id=source_id,
        external_id=item.get("UniqueId"),
        kind="report",
        payload={
            "ref_type": "calfire",
            "ref_id": item.get("UniqueId"),
            "name": item.get("Name"),
            "type": item.get("Type"),
            "acreage": _f(item.get("AcresBurned")),
            "containment_pct": _f(item.get("PercentContained")),
            "county": item.get("County"),
            "lat": _f(item.get("Latitude")),
            "lon": _f(item.get("Longitude")),
            "is_active": is_active,
            "discovered_at": _iso(item.get("Started")),
            "modified_at": _iso(item.get("Updated")),
            "extinguished_at": _iso(item.get("ExtinguishedDate")),
            "url": item.get("Url"),
        },
        observed_hint=_iso(item.get("Updated")) or _iso(item.get("Started")),
        url=item.get("Url"),
    )


def fetch_incidents(
    capture: RawCapture, *, inactive: bool = False, year: int | None = None
) -> list[dict[str, Any]]:
    params = []
    if year is not None:
        params.append(f"year={int(year)}")
    else:
        params.append(f"inactive={'true' if inactive else 'false'}")
    url = LIST_URL + "?" + "&".join(params)
    body, _ctype = capture.fetch_captured(url, headers={"User-Agent": BROWSER_UA})
    data = json.loads(body)
    return data if isinstance(data, list) else data.get("Incidents", [])


@register("calfire")
class CalFireAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        inactive = bool(config.get("inactive", False))
        year = config.get("year")
        items = fetch_incidents(self, inactive=inactive, year=year)
        envelopes = [incident_to_payload(item, config["source_id"]) for item in items]
        return envelopes, cursor  # snapshot; dedup handles repeats
