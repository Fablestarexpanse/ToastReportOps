"""Meshtastic packet decoding: MQTT ServiceEnvelope -> plain dicts.

Public-MQTT packets on the default LongFast channel are AES128-CTR encrypted
with the WELL-KNOWN default key — 'encrypted' in name only; every Meshtastic
node and map on earth shares it. Decrypting it is how the public mesh is meant
to be read. A packet on a private key simply fails to decode and is dropped:
we never see, store, or guess at genuinely private traffic.

Everything decoded is hostile community input: text is length-capped and
stored inert (parameterized, never rendered as HTML, never fed to a shell or
an LLM prompt unfenced), positions are numbers or nothing.
"""

from __future__ import annotations

import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

# The published default channel key (LongFast). Not a secret anywhere.
DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")

MAX_TEXT = 500


def _decrypt(packet: mesh_pb2.MeshPacket) -> bytes | None:
    """AES128-CTR with nonce = packet id (8 LE) + sender (8 LE)."""
    nonce = struct.pack("<Q", packet.id) + struct.pack("<Q", getattr(packet, "from"))
    try:
        cipher = Cipher(algorithms.AES(DEFAULT_PSK), modes.CTR(nonce))
        dec = cipher.decryptor()
        return dec.update(packet.encrypted) + dec.finalize()
    except Exception:
        return None


def _node_id(num: int) -> str:
    return f"!{num & 0xFFFFFFFF:08x}"


def decode_envelope(raw: bytes) -> dict[str, Any] | None:
    """One MQTT payload -> {'node', 'gateway', 'kind', ...} or None."""
    try:
        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(raw)
    except Exception:
        return None
    packet = env.packet
    if not packet.id and not getattr(packet, "from"):
        return None

    data = None
    if packet.HasField("decoded"):
        data = packet.decoded
    elif packet.encrypted:
        plain = _decrypt(packet)
        if plain is None:
            return None
        data = mesh_pb2.Data()
        try:
            data.ParseFromString(plain)
        except Exception:
            return None  # a private channel, or garbage — either way not ours
    if data is None:
        return None

    out: dict[str, Any] = {
        "node": _node_id(getattr(packet, "from")),
        "gateway": env.gateway_id or None,
        "channel": env.channel_id or None,
        "snr": round(packet.rx_snr, 1) if packet.rx_snr else None,
        "hops": (packet.hop_start - packet.hop_limit)
        if packet.hop_start and packet.hop_start >= packet.hop_limit
        else None,
    }

    port = data.portnum
    if port == portnums_pb2.PortNum.TEXT_MESSAGE_APP:
        try:
            text = data.payload.decode("utf-8", errors="replace")[:MAX_TEXT]
        except Exception:
            return None
        out.update(kind="text", text=text)
        return out

    if port == portnums_pb2.PortNum.POSITION_APP:
        pos = mesh_pb2.Position()
        try:
            pos.ParseFromString(data.payload)
        except Exception:
            return None
        if not pos.latitude_i and not pos.longitude_i:
            return None
        out.update(
            kind="position",
            lat=pos.latitude_i * 1e-7,
            lon=pos.longitude_i * 1e-7,
            alt_m=pos.altitude or None,
        )
        return out

    if port == portnums_pb2.PortNum.NODEINFO_APP:
        user = mesh_pb2.User()
        try:
            user.ParseFromString(data.payload)
        except Exception:
            return None
        try:
            # hardware newer than our protobufs has no enum name yet
            hw = mesh_pb2.HardwareModel.Name(user.hw_model) if user.hw_model else None
        except ValueError:
            hw = f"HW_{user.hw_model}"
        out.update(
            kind="nodeinfo",
            long_name=(user.long_name or "")[:60] or None,
            short_name=(user.short_name or "")[:8] or None,
            hw_model=hw,
        )
        return out

    if port == portnums_pb2.PortNum.TELEMETRY_APP:
        tel = telemetry_pb2.Telemetry()
        try:
            tel.ParseFromString(data.payload)
        except Exception:
            return None
        if not tel.HasField("device_metrics"):
            return None
        dm = tel.device_metrics
        out.update(
            kind="telemetry",
            battery=dm.battery_level or None,
            voltage=round(dm.voltage, 2) if dm.voltage else None,
        )
        return out

    return None  # routing/admin/other ports are mesh plumbing, not signal
