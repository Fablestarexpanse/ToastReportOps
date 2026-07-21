"""Mesh listener service: public Meshtastic MQTT -> mesh tables.

Runs as its own compose service (a persistent subscription does not fit the
job queue). Subscribes to the US LongFast topic on the public broker and keeps
a NorCal picture:

- a node becomes in_region when it reports a position inside the bbox;
- a GATEWAY that is in-region hears its neighbourhood by RF, so everything it
  reports is kept (marked via the gateway) even before those nodes send their
  own position — that is the locality heuristic that makes a regional view
  possible on a firehose topic;
- everything else is dropped on the floor, uncounted and unstored.

When Brian's own nodes arrive they appear like any other node the moment they
join the mesh — and a later direct serial/TCP hookup only ADDS packets this
listener cannot hear from here.

PSPS honesty: this listener rides the INTERNET to the public broker. The mesh
itself keeps working without internet; our view of it through MQTT does not.
The MESH tab says so.
"""

from __future__ import annotations

import os
import sqlite3
import time

import paho.mqtt.client as mqtt

from toast.core.config import Settings
from toast.core.db import connect, utcnow
from toast.core.logging import get_logger
from toast.mesh.decode import decode_envelope

BROKER = os.environ.get("MESH_MQTT_HOST", "mqtt.meshtastic.org")
# the public broker's published community credentials — not a secret
USERNAME = os.environ.get("MESH_MQTT_USER", "meshdev")
PASSWORD = os.environ.get("MESH_MQTT_PASS", "large4cats")
# msh/US/# rather than pinning the channel path: gateways publish under
# regional roots too (msh/US/CA/..., msh/US/2/...), and pinning missed them all
TOPIC = os.environ.get("MESH_MQTT_TOPIC", "msh/US/#")

# NorCal: Sacramento valley + northern Sierra + coast range
BBOX = (38.0, 42.0, -124.5, -119.5)  # lat min/max, lon min/max
MESSAGE_RETAIN_H = 48
PRUNE_EVERY_S = 600


def _in_bbox(lat: float, lon: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


class MeshListener:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.log = get_logger("mesh")
        self.kept = self.dropped = self.received = 0
        self._last_prune = 0.0

    # -------------------------------------------------- region logic

    def _gateway_in_region(self, gateway: str | None) -> bool:
        if not gateway:
            return False
        row = self.conn.execute(
            "SELECT in_region FROM mesh_nodes WHERE node_id = ?", (gateway,)
        ).fetchone()
        return bool(row and row["in_region"])

    def _upsert(self, node: str, fields: dict, *, gateway: str | None) -> None:
        now = utcnow()
        self.conn.execute(
            """INSERT INTO mesh_nodes (node_id, first_heard, last_heard, last_gateway)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET last_heard = excluded.last_heard,
                   last_gateway = COALESCE(excluded.last_gateway, mesh_nodes.last_gateway)""",
            (node, now, now, gateway),
        )
        sets = ", ".join(f"{k} = ?" for k in fields)
        if sets:
            self.conn.execute(
                f"UPDATE mesh_nodes SET {sets} WHERE node_id = ?",
                (*fields.values(), node),
            )

    # -------------------------------------------------- mqtt callbacks

    def on_message(self, client, userdata, msg) -> None:
        self.received += 1
        if self.received in (1, 100) or self.received % 2000 == 0:
            self.log.info(
                "mesh_rx", received=self.received, kept=self.kept, dropped=self.dropped,
                sample_topic=msg.topic[:60],
            )
        if "/json/" in msg.topic or "/stat/" in msg.topic:
            return  # JSON mirrors and gateway status — the protobuf path is ours
        try:
            self._handle(msg.payload)
        except Exception as exc:  # one bad packet must never kill the listener
            self.log.warning("mesh_packet_error", error=str(exc)[:120])

    def _handle(self, payload: bytes) -> None:
        decoded = decode_envelope(payload)
        if decoded is None:
            return
        node, gateway = decoded["node"], decoded.get("gateway")
        kind = decoded["kind"]

        if kind == "position":
            local = _in_bbox(decoded["lat"], decoded["lon"])
            if not local and not self._gateway_in_region(gateway):
                self.dropped += 1
                return
            self._upsert(
                node,
                {
                    "lat": decoded["lat"],
                    "lon": decoded["lon"],
                    "alt_m": decoded.get("alt_m"),
                    "in_region": 1 if local else 0,
                    "snr": decoded.get("snr"),
                    "hops": decoded.get("hops"),
                },
                gateway=gateway,
            )
            self.kept += 1
        elif kind in ("nodeinfo", "telemetry", "text"):
            # kept only when locality is already established
            known = self.conn.execute(
                "SELECT in_region FROM mesh_nodes WHERE node_id = ?", (node,)
            ).fetchone()
            if not (known and known["in_region"]) and not self._gateway_in_region(gateway):
                self.dropped += 1
                return
            fields = {
                k: decoded[k]
                for k in ("long_name", "short_name", "hw_model", "battery", "voltage")
                if decoded.get(k) is not None
            }
            fields["snr"] = decoded.get("snr")
            fields["hops"] = decoded.get("hops")
            self._upsert(node, fields, gateway=gateway)
            if kind == "text":
                self.conn.execute(
                    """INSERT INTO mesh_messages (node_id, gateway_id, channel, text, rx_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (node, gateway, decoded.get("channel"), decoded["text"], utcnow()),
                )
            self.kept += 1
        self.conn.commit()
        self._maybe_prune()

    def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = time.monotonic()
        self.conn.execute(
            "DELETE FROM mesh_messages WHERE unixepoch('now') - unixepoch(rx_at) > ?",
            (MESSAGE_RETAIN_H * 3600,),
        )
        self.conn.commit()
        self.log.info("mesh_stats", kept=self.kept, dropped=self.dropped)


def main() -> None:
    settings = Settings()
    conn = connect(settings.toast_db_path)
    listener = MeshListener(conn)
    log = get_logger("mesh")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_message = listener.on_message

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("mesh_connected", broker=BROKER, topic=TOPIC)
        c.subscribe(TOPIC)

    client.on_connect = on_connect
    while True:  # the broker drops connections; always come back
        try:
            client.connect(BROKER, 1883, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except Exception as exc:
            log.warning("mesh_reconnect", error=str(exc)[:120])
            time.sleep(15)


if __name__ == "__main__":
    main()
