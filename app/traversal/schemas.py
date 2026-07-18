"""The frozen traversal-event contract + the request-context stamp.

A traversal event is the atomic unit a graph view animates: an agent lands on a
node (``entry``) or follows one directed edge to a neighbor (``hop``). Events are
routed by ``sessionId`` and ordered by a per-session ``seq`` the channel assigns
(see ``app.traversal.channel``). This module is a leaf — it imports nothing from
the emitter (``app.context_engine.graph_tools``) or any transport, so both the
emit side and future SSE/WS subscribe sides can depend on it without a cycle.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

Source = Literal["mcp", "web"]  # which surface drove the traversal
TraversalEventKind = Literal["entry", "hop"]


class TraversalEvent(BaseModel):
    """One step of a session's memory traversal, emitted as a side effect of a
    graph-tool call. ``edgeKind``/``fromNodeId`` are set for hops and None for
    entries (an entry has no incoming edge — it's where the agent started)."""

    sessionId: str  # routing key
    seq: int  # monotonic per-session ordinal, assigned by the channel
    kind: TraversalEventKind
    nodeId: str  # the node landed on (entry) or reached (hop)
    edgeKind: str | None  # WalkEdgeKind string for hops; None for entries
    fromNodeId: str | None  # the origin node for hops; None for entries
    source: Source
    timestamp: str  # ISO-8601 UTC


@dataclass(frozen=True)
class TraversalTag:
    """The session routing key + origin stamped onto every event of one traversal.

    Threaded from the request context (MCP key context or web request) into the
    graph tools; its presence is what turns emission on — a tool called without a
    tag stays silent, so non-session callers and tests are unaffected.
    """

    session_id: str
    source: Source
