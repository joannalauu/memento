"""Traversal channel (app/traversal/channel.py): the in-process, sessionId-keyed
pub/sub that carries graph-traversal events. A fresh TraversalChannel() is built
per test for isolation (the shipped `traversal_channel` is a process singleton)."""

from app.traversal.channel import TraversalChannel
from app.traversal.schemas import TraversalEvent, TraversalTag


def mcp_tag(session_id="s1"):
    return TraversalTag(session_id=session_id, source="mcp")


def capture(channel, session_id):
    """Subscribe a list-appending callback; return (events, unsubscribe)."""
    events: list[TraversalEvent] = []
    unsub = channel.subscribe(session_id, events.append)
    return events, unsub


def test_publish_stamps_event_fields_and_returns_it():
    channel = TraversalChannel()
    events, _ = capture(channel, "s1")

    returned = channel.publish(
        mcp_tag(),
        kind="hop",
        node_id="dec:2",
        edge_kind="governs",
        from_node_id="dec:1",
    )

    assert events == [returned]
    e = events[0]
    assert (e.sessionId, e.kind, e.nodeId, e.edgeKind, e.fromNodeId, e.source) == (
        "s1",
        "hop",
        "dec:2",
        "governs",
        "dec:1",
        "mcp",
    )
    assert e.seq == 0
    assert e.timestamp  # ISO-8601 stamp is present


def test_seq_is_monotonic_per_session():
    channel = TraversalChannel()
    events, _ = capture(channel, "s1")

    for _ in range(3):
        channel.publish(
            mcp_tag(), kind="entry", node_id="dec:1", edge_kind=None, from_node_id=None
        )

    assert [e.seq for e in events] == [0, 1, 2]


def test_sessions_are_isolated():
    channel = TraversalChannel()
    events_a, _ = capture(channel, "sa")
    events_b, _ = capture(channel, "sb")

    channel.publish(
        TraversalTag("sa", "mcp"),
        kind="entry",
        node_id="dec:1",
        edge_kind=None,
        from_node_id=None,
    )
    channel.publish(
        TraversalTag("sb", "web"),
        kind="entry",
        node_id="dec:2",
        edge_kind=None,
        from_node_id=None,
    )

    # each subscriber sees only its own session, and each session's seq starts at 0
    assert [e.nodeId for e in events_a] == ["dec:1"]
    assert [e.nodeId for e in events_b] == ["dec:2"]
    assert events_a[0].seq == 0 and events_b[0].seq == 0
    assert events_b[0].source == "web"


def test_fans_out_to_all_subscribers():
    channel = TraversalChannel()
    events_1, _ = capture(channel, "s1")
    events_2, _ = capture(channel, "s1")

    channel.publish(
        mcp_tag(), kind="hop", node_id="dec:2", edge_kind="made", from_node_id="dec:1"
    )

    assert len(events_1) == 1 and len(events_2) == 1
    assert events_1[0].seq == events_2[0].seq == 0  # one seq per event, shared


def test_unsubscribe_stops_delivery_and_is_idempotent():
    channel = TraversalChannel()
    events, unsub = capture(channel, "s1")

    channel.publish(
        mcp_tag(), kind="entry", node_id="dec:1", edge_kind=None, from_node_id=None
    )
    unsub()
    unsub()  # second call must not raise
    channel.publish(
        mcp_tag(), kind="entry", node_id="dec:2", edge_kind=None, from_node_id=None
    )

    assert [e.nodeId for e in events] == ["dec:1"]  # nothing after unsubscribe


def test_emit_without_subscriber_drops_but_advances_seq():
    channel = TraversalChannel()
    # no subscriber yet: this event is dropped, but its seq is still consumed
    channel.publish(
        mcp_tag(), kind="entry", node_id="dec:1", edge_kind=None, from_node_id=None
    )

    events, _ = capture(channel, "s1")
    later = channel.publish(
        mcp_tag(), kind="entry", node_id="dec:2", edge_kind=None, from_node_id=None
    )

    assert [e.nodeId for e in events] == ["dec:2"]
    assert later.seq == 1  # the dropped event consumed seq 0
