"""Mesh chat is scanned for fire the moment it lands: a fire-worded text
becomes a T5 WATCH signal (same shape radio uses), a plain one does not."""

from toast.mesh.listener import MeshListener, _looks_fire


def _msg(db, node, text, is_fire=0):
    cur = db.execute(
        "INSERT INTO mesh_messages (node_id, gateway_id, channel, text, rx_at, is_fire)"
        " VALUES (?, NULL, 'LongFast', ?, '2026-07-24T00:00:00Z', ?)",
        (node, text, is_fire),
    )
    return cur.lastrowid


def _node(db, node, lat=39.7, lon=-121.6, name="Ridge Relay"):
    db.execute(
        "INSERT INTO mesh_nodes (node_id, long_name, lat, lon, in_region, first_heard, last_heard)"
        " VALUES (?, ?, ?, ?, 1, '2026-07-24T00:00:00Z', '2026-07-24T00:00:00Z')",
        (node, name, lat, lon),
    )


def test_keyword_gate():
    assert _looks_fire("smoke column to the north east")
    assert _looks_fire("EVAC now, structure threatened")
    assert not _looks_fire("anyone near the lake this weekend?")


def test_fire_emoji_trips_the_gate():
    # a 🔥 alone is enough — people type it before they type the word
    assert _looks_fire("🔥 up on the ridge")
    assert _looks_fire("something's off on Skyway 🚒")
    assert not _looks_fire("great BBQ today 🎉")


def test_fire_message_feeds_watch_with_position(db):
    _node(db, "!aa", lat=39.75, lon=-121.61, name="Paradise Node")
    mid = _msg(db, "!aa", "Heavy smoke and flames off Skyway, spreading fast")
    listener = MeshListener(db)
    listener._feed_watch(mid, "!aa", "Heavy smoke and flames off Skyway, spreading fast")

    # the message is flagged and linked
    row = db.execute(
        "SELECT is_fire, watch_fact_id FROM mesh_messages WHERE id = ?", (mid,)
    ).fetchone()
    assert row["is_fire"] == 1 and row["watch_fact_id"]

    # a T5 'start' fact with no incident — exactly what WATCH clusters
    fact = db.execute("SELECT * FROM facts WHERE id = ?", (row["watch_fact_id"],)).fetchone()
    assert fact["tier"] == 5 and fact["kind"] == "start" and fact["incident_id"] is None
    assert fact["confidence"] == 0.3
    assert "[mesh Paradise Node]" in fact["content"]
    # the sender's position rode along so a promoted watch can pin to the map
    assert fact["lat"] == 39.75 and fact["lon"] == -121.61

    # a single tier-5 mesh source was created and reused
    srcs = db.execute("SELECT COUNT(*) FROM sources WHERE adapter = 'mesh'").fetchone()[0]
    assert srcs == 1
    listener._feed_watch(_msg(db, "!aa", "more smoke"), "!aa", "more smoke")
    assert db.execute("SELECT COUNT(*) FROM sources WHERE adapter = 'mesh'").fetchone()[0] == 1


def test_watch_feed_survives_missing_node(db):
    # a fire message from a node we have no position for still escalates,
    # just without coordinates — never raises, never drops
    mid = _msg(db, "!ghost", "fire near the ridge")
    MeshListener(db)._feed_watch(mid, "!ghost", "fire near the ridge")
    fact = db.execute(
        "SELECT * FROM facts WHERE id = (SELECT watch_fact_id FROM mesh_messages WHERE id = ?)",
        (mid,),
    ).fetchone()
    assert fact["tier"] == 5 and fact["lat"] is None
