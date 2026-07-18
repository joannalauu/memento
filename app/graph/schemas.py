"""GraphPayload — the explicit {nodes, links} shape react-force-graph consumes.

Node types: decision, file, pr, engineer, feature. Symbols are NOT nodes —
they ride as metadata on the `governs` edge (`GraphLink.symbols`), keeping the
canvas legible while staying queryable via `memoryIndex.anchors.symbols`.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NodeType = Literal["decision", "file", "pr", "engineer", "feature"]
EdgeKind = Literal["governs", "introduced", "made", "belongs_to", "superseded_by"]


class GraphNodeMeta(BaseModel):
    # decision nodes
    prNumber: int | None = None
    author: str | None = None
    date: datetime | None = None
    stalenessStatus: Literal["fresh", "stale", "gap"] | None = None
    confidence: Literal["verified", "unverified"] | None = None
    # file nodes
    path: str | None = None
    # feature nodes
    decisionCount: int | None = None


class GraphNode(BaseModel):
    id: str
    type: NodeType
    label: str
    val: float  # render size — degree-derived (see crud.build_graph)
    meta: GraphNodeMeta = Field(default_factory=GraphNodeMeta)


class GraphLink(BaseModel):
    source: str  # node id
    target: str  # node id
    kind: EdgeKind
    symbols: list[str] | None = None  # governs only


class GraphPayload(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]


class RelatedDecision(BaseModel):
    """A decision reachable from a non-decision node — the hop targets that let
    an agent (or a human clicking) walk from a file/pr/engineer/feature into the
    decisions that touch it. `id` is the graph node id (`dec:<oid>`)."""

    id: str
    label: str
    prNumber: int | None = None
    author: str | None = None
    date: datetime
    stalenessStatus: Literal["fresh", "stale", "gap"] | None = None


class NodeDetail(BaseModel):
    """Full detail for one node, fetched on click. Decision nodes carry the
    complete snapshot + provenance (the graph only ships a truncated label);
    every other node type carries the list of decisions it connects to."""

    id: str
    type: NodeType
    label: str
    # decision nodes
    contentSnapshot: str | None = None
    prNumber: int | None = None
    prUrl: str | None = None
    author: str | None = None
    date: datetime | None = None
    feature: str | None = None
    files: list[str] | None = None
    symbols: list[str] | None = None
    stalenessStatus: Literal["fresh", "stale", "gap"] | None = None
    confidence: Literal["verified", "unverified"] | None = None
    supersededBy: str | None = None  # "dec:<oid>"
    # non-decision nodes (file, pr, engineer, feature)
    relatedDecisions: list[RelatedDecision] | None = None
