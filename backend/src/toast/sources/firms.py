"""NASA FIRMS satellite hotspots (T1 official sensor data).

Area CSV API; requires a free MAP_KEY (FIRMS_MAP_KEY in .env, referenced from
the source config as {"map_key_env": "FIRMS_MAP_KEY"}). Default coverage:
California bounding box, VIIRS S-NPP near-real-time.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any

from toast.core.envelope import Envelope
from toast.sources import register
from toast.sources.capture import RawCapture

CA_BBOX = "-124.5,32.4,-114.0,42.1"  # lon_min,lat_min,lon_max,lat_max
DEFAULT_DATASET = "VIIRS_SNPP_NRT"


@register("firms")
class FirmsAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        # the poll handler injects an operator-entered key from OPTIONS; the
        # environment remains the fallback for .env-configured installs
        key_env = config.get("map_key_env", "FIRMS_MAP_KEY")
        map_key = config.get("map_key") or os.environ.get(key_env, "")
        if not map_key:
            raise RuntimeError(
                f"FIRMS map key missing: add it in OPTIONS → API KEYS, or set {key_env} in .env"
            )
        dataset = config.get("dataset", DEFAULT_DATASET)
        bbox = config.get("bbox", CA_BBOX)
        days = int(config.get("days", 1))
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/{dataset}/{bbox}/{days}"
        # record_url=False: the FIRMS URL embeds the map key — never store it
        body, _ctype = self.fetch_captured(url, record_url=False)

        envelopes: list[Envelope] = []
        reader = csv.DictReader(io.StringIO(body.decode("utf-8", errors="replace")))
        for row in reader:
            acq_date = row.get("acq_date", "")
            acq_time = (row.get("acq_time") or "0000").zfill(4)
            observed = f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}:00.000Z" if acq_date else None
            external_id = ":".join(
                (
                    row.get("latitude", ""),
                    row.get("longitude", ""),
                    acq_date,
                    acq_time,
                    row.get("satellite", ""),
                )
            )
            envelopes.append(
                Envelope(
                    source_id=config["source_id"],
                    external_id=external_id,
                    kind="hotspot",
                    payload={
                        "lat": float(row["latitude"]) if row.get("latitude") else None,
                        "lon": float(row["longitude"]) if row.get("longitude") else None,
                        "frp": float(row["frp"]) if row.get("frp") else None,
                        "confidence": row.get("confidence"),
                        "satellite": row.get("satellite"),
                        "instrument": row.get("instrument"),
                        "daynight": row.get("daynight"),
                        "observed_at": observed,
                    },
                    observed_hint=observed,
                    # url intentionally omitted: it embeds the map key
                )
            )
        return envelopes, cursor  # windowed snapshot; envelope dedup handles overlap
