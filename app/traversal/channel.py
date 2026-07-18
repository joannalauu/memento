"""In-process pub/sub for traversal events, keyed by ``sessionId``.

The graph tools ``publish`` an event per resolved node/hop; a transport (T4.5 SSE,
T4.6a WebSockets) ``subscribe``s a session to fan events out to a connected view.
The two are fully decoupled — the tools never know whether anyone is listening.

Single-instance only: subscribers live in this process's memory, so a multi-worker
deployment won't fan an event emitted on worker A out to a view connected to worker
B. Multi-instance fan-out (a Redis pub/sub backing) is a later ticket; ``publish``
and ``subscribe`` are the only two seams it needs to swap. Both run synchronously on
the single asyncio event loop (no threads), so no locking is required.

Ephemeral by design: there is no buffering. An event emitted while a session has no
subscriber is dropped (``seq`` still advances, so a view that connects mid-traversal
sees the gap and knows it missed steps). ``_seq`` retains one small int per session
id for the process's life — acceptable for an ephemeral channel; a real backing
store would age these out.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from app.traversal.schemas import (
    TraversalEvent,
    TraversalEventKind,
    TraversalTag,
)

Subscriber = Callable[[TraversalEvent], None]


class TraversalChannel:
    """A sessionId-keyed event bus. Owns the monotonic per-session seq counter."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscriber]] = defaultdict(set)
        self._seq: dict[str, int] = defaultdict(int)

    def publish(
        self,
        tag: TraversalTag,
        *,
        kind: TraversalEventKind,
        node_id: str,
        edge_kind: str | None,
        from_node_id: str | None,
    ) -> TraversalEvent:
        """Stamp the next seq for ``tag.session_id``, build the event, and hand it
        to every current subscriber. Returns the event (handy for callers/tests)."""
        seq = self._seq[tag.session_id]
        self._seq[tag.session_id] = seq + 1
        event = TraversalEvent(
            sessionId=tag.session_id,
            seq=seq,
            kind=kind,
            nodeId=node_id,
            edgeKind=edge_kind,
            fromNodeId=from_node_id,
            source=tag.source,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        # snapshot: a subscriber may unsubscribe itself while being notified
        for cb in tuple(self._subscribers.get(tag.session_id, ())):
            cb(event)
        return event

    def subscribe(self, session_id: str, cb: Subscriber) -> Callable[[], None]:
        """Register ``cb`` for a session; returns an idempotent unsubscribe."""
        self._subscribers[session_id].add(cb)

        def unsubscribe() -> None:
            subs = self._subscribers.get(session_id)
            if subs is not None:
                subs.discard(cb)
                if not subs:  # drop the empty set; seq is retained deliberately
                    self._subscribers.pop(session_id, None)

        return unsubscribe


# Process-wide singleton. The emit side (graph_tools) and subscribe side
# (transports) both import this instance — the module-level singleton mirrors the
# module-level Mongo seams in graph_tools, keeping emission a pure side effect with
# no channel threaded through every tool call.
traversal_channel = TraversalChannel()
