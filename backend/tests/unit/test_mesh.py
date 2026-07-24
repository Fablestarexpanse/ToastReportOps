"""Mesh decode + region gating.

The decode test builds REAL protobuf envelopes — including one encrypted with
the published default key, the way every public-mesh packet actually arrives —
so the decryption path is proven, not assumed. The region tests pin the
locality heuristic: bbox positions admit a node; an in-region gateway vouches
for its RF neighbourhood; everything else is dropped unstored.
"""

import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

from toast.mesh import listener as mesh_listener
from toast.mesh.decode import DEFAULT_PSK, decode_envelope


def _envelope(from_num, packet_id, data: mesh_pb2.Data, *, encrypt=False, gateway="!feed0001"):
    env = mqtt_pb2.ServiceEnvelope()
    env.gateway_id = gateway
    env.channel_id = "LongFast"
    p = env.packet
    setattr(p, "from", from_num)
    p.id = packet_id
    if encrypt:
        nonce = struct.pack("<Q", packet_id) + struct.pack("<Q", from_num)
        enc = Cipher(algorithms.AES(DEFAULT_PSK), modes.CTR(nonce)).encryptor()
        p.encrypted = enc.update(data.SerializeToString()) + enc.finalize()
    else:
        p.decoded.CopyFrom(data)
    return env.SerializeToString()


def _text_data(text):
    d = mesh_pb2.Data()
    d.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    d.payload = text.encode()
    return d


def _pos_data(lat, lon):
    pos = mesh_pb2.Position()
    pos.latitude_i = int(lat / 1e-7)
    pos.longitude_i = int(lon / 1e-7)
    d = mesh_pb2.Data()
    d.portnum = portnums_pb2.PortNum.POSITION_APP
    d.payload = pos.SerializeToString()
    return d


class TestDecode:
    def test_default_key_encrypted_text_decodes(self):
        """The path every real public-mesh packet takes."""
        raw = _envelope(0xAABBCCDD, 777, _text_data("Smoke visible from Sawmill Peak"),
                        encrypt=True)
        out = decode_envelope(raw)
        assert out["kind"] == "text"
        assert out["text"] == "Smoke visible from Sawmill Peak"
        assert out["node"] == "!aabbccdd"
        assert out["gateway"] == "!feed0001"

    def test_position_decodes_to_degrees(self):
        raw = _envelope(1, 2, _pos_data(39.7596, -121.6219))
        out = decode_envelope(raw)
        assert out["kind"] == "position"
        assert abs(out["lat"] - 39.7596) < 1e-4 and abs(out["lon"] + 121.6219) < 1e-4

    def test_a_private_channel_is_dropped_not_guessed(self):
        """Encrypted with a different key: parse fails cleanly, nothing stored."""
        env = mqtt_pb2.ServiceEnvelope()
        p = env.packet
        setattr(p, "from", 5)
        p.id = 6
        p.encrypted = b"\x99" * 32  # noise to our key
        assert decode_envelope(env.SerializeToString()) is None

    def test_garbage_is_none(self):
        assert decode_envelope(b"not a protobuf") is None


class TestRegionGating:
    def _listener(self, seeded):
        return mesh_listener.MeshListener(seeded)

    def test_bbox_position_admits_a_node(self, seeded):
        li = self._listener(seeded)
        li._handle(_envelope(0x01, 1, _pos_data(39.76, -121.62)))  # Paradise
        row = seeded.execute("SELECT * FROM mesh_nodes").fetchone()
        assert row["node_id"] == "!00000001" and row["in_region"] == 1

    def test_socal_position_is_now_in_region(self, seeded):
        # the statewide widening: a San Diego node is admitted just like a
        # NorCal one — it would have been dropped under the old NorCal box
        li = self._listener(seeded)
        li._handle(_envelope(0x03, 7, _pos_data(32.72, -117.16)))  # San Diego
        row = seeded.execute("SELECT * FROM mesh_nodes").fetchone()
        assert row is not None and row["in_region"] == 1

    def test_far_position_with_unknown_gateway_is_dropped(self, seeded):
        li = self._listener(seeded)
        # Columbus OH — well outside the statewide CA box, no local gateway
        li._handle(_envelope(0x02, 2, _pos_data(39.96, -82.99)))
        assert seeded.execute("SELECT COUNT(*) FROM mesh_nodes").fetchone()[0] == 0
        assert li.dropped == 1

    def test_an_in_region_gateway_vouches_for_its_neighbourhood(self, seeded):
        li = self._listener(seeded)
        # the gateway proves itself local first
        li._handle(_envelope(0xFEED0001, 3, _pos_data(39.7, -121.8), gateway="!feed0001"))
        # then a neighbour with NO position, heard through it, is kept
        li._handle(_envelope(0x0A, 4, _text_data("Anyone copy on Skyway?"),
                             encrypt=True, gateway="!feed0001"))
        msg = seeded.execute("SELECT * FROM mesh_messages").fetchone()
        assert msg is not None and "Skyway" in msg["text"]
        assert msg["node_id"] == "!0000000a"

    def test_text_from_nowhere_is_dropped_unstored(self, seeded):
        li = self._listener(seeded)
        li._handle(_envelope(0x0B, 5, _text_data("hello from Ohio"), gateway="!ffffffff"))
        assert seeded.execute("SELECT COUNT(*) FROM mesh_messages").fetchone()[0] == 0
