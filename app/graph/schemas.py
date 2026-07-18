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
