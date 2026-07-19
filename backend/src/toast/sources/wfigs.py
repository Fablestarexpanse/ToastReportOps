"""WFIGS/IRWIN current wildland fire incident locations (T1 official).

ArcGIS REST FeatureServer maintained by NIFC. Cursor = max ModifiedOnDateTime
(epoch ms) seen, so each poll fetches only changed incidents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from toast.core.envelope import Envelope
from toast.sources import register
from toast.sources.capture import RawCapture

DEFAULT_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)

OUT_FIELDS = [
    "IrwinID",
    "IncidentName",
    "IncidentTypeCategory",
    "IncidentSize",
    "DiscoveryAcres",
    "PercentContained",
    "FireDiscoveryDateTime",
    "ModifiedOnDateTime_dt",
    "POOCounty",
    "POOState",
    "InitialLatitude",
    "InitialLongitude",
    "TotalIncidentPersonnel",
    "FireCause",
]


def _epoch_ms_to_iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@register("wfigs")
class WfigsAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        url = config.get("url", DEFAULT_URL)
        state = config.get("state", "US-CA")
        where = f"POOState='{state}'"
        if cursor:
            # date field — needs a TIMESTAMP literal, not epoch ms
            ts = datetime.fromtimestamp(int(cursor) / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
            where += f" AND ModifiedOnDateTime_dt > TIMESTAMP '{ts}'"
        params = {
            "where": where,
            "outFields": ",".join(OUT_FIELDS),
            "f": "json",
            "resultRecordCount": "500",
            "orderByFields": "ModifiedOnDateTime_dt ASC",
        }
        body, _ctype = self.fetch_captured(url + "?" + urlencode(params))
        data = json.loads(body)
        if "error" in data:
            raise RuntimeError(f"WFIGS query error: {data['error']}")

        envelopes: list[Envelope] = []
        max_modified = int(cursor) if cursor else 0
        for feature in data.get("features", []):
            attrs = feature.get("attributes", {})
            geom = feature.get("geometry") or {}
            modified = attrs.get("ModifiedOnDateTime_dt") or 0
            max_modified = max(max_modified, int(modified))
            acreage = attrs.get("IncidentSize")
            if acreage is None:
                acreage = attrs.get("DiscoveryAcres")
            envelopes.append(
                Envelope(
                    source_id=config["source_id"],
                    external_id=attrs.get("IrwinID"),
                    kind="report",
                    payload={
                        "irwin_id": attrs.get("IrwinID"),
                        "name": attrs.get("IncidentName"),
                        "type": attrs.get("IncidentTypeCategory"),
                        "acreage": acreage,
                        "containment_pct": attrs.get("PercentContained"),
                        "personnel": attrs.get("TotalIncidentPersonnel"),
                        "cause": attrs.get("FireCause"),
                        "discovered_at": _epoch_ms_to_iso(attrs.get("FireDiscoveryDateTime")),
                        "modified_at": _epoch_ms_to_iso(modified),
                        "county": attrs.get("POOCounty"),
                        "lat": geom.get("y", attrs.get("InitialLatitude")),
                        "lon": geom.get("x", attrs.get("InitialLongitude")),
                    },
                    observed_hint=_epoch_ms_to_iso(modified),
                    url=url,
                )
            )
        next_cursor = str(max_modified) if max_modified else cursor
        return envelopes, next_cursor
