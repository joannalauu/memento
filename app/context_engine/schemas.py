"""Shapes shared by the context-engine pipeline stages.

`RelatedMemory` is stage-2 output (retrieval evidence + joined index
structure); `ConsistencyVerdict`/`ConsistencyConflict` are stage-3 output.
`ConsistencyConflict` is deliberately richer than
`app.claude_hook.models.Conflict` (it carries the contradiction's nature and
severity, not just a summary) — claude_hook can migrate to it later.
"""

from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.backboard.models import MemoryConfidence, MemorySource
from app.graph.schemas import NodeType

ConsistencyMode = Literal["audit", "preflight"]

# The five graph edge kinds plus `supersedes` — the directional inverse of
# `superseded_by`. A walk from decision B must distinguish "B is superseded by
# X" (outgoing) from "B supersedes A" (incoming); the graph projection only
# needs the one direction, an agent tracing an evolution chain needs both.
WalkEdgeKind = Literal[
    "governs", "introduced", "made", "belongs_to", "superseded_by", "supersedes"
]


class RelatedMemory(BaseModel):
    """One memory relevant to a change, with the evidence that ranked it."""

    bbMemoryId: str
    content: str  # contentSnapshot when indexed, Backboard hit content otherwise
    matchedFiles: list[str] = Field(default_factory=list)  # exact anchor-file hits
    matchedSymbols: list[str] = Field(default_factory=list)  # exact anchor-symbol hits
    semanticAnchors: list[str] = Field(
        default_factory=list
    )  # anchors whose search returned it
    score: float
    # structure joined from MemoryIndex; None for unindexed semantic-only hits
    prNumber: int | None = None
    feature: str | None = None
    authorUserId: PydanticObjectId | None = None
    source: MemorySource | None = None
    confidence: MemoryConfidence | None = None


class ConsistencyConflict(BaseModel):
    """A prior decision the change contradicts, cited by memory id."""

    bbMemoryId: str  # echoed verbatim from an input memory — enforced in code
    priorDecision: str  # one-sentence paraphrase of what the memory established
    priorPr: int | None = None
    nature: str  # how the change contradicts it, one sentence
    severity: Literal["direct", "partial"]  # direct = reverses; partial = erodes


class ConsistencyVerdict(BaseModel):
    verdict: Literal["consistent", "conflict", "no_prior_context"]
    confidence: Literal["high", "medium", "low"]
    conflicts: list[ConsistencyConflict] = Field(default_factory=list)
    reasoning: str = ""
    supersedes: list[int] = Field(default_factory=list)  # PRs intentionally replaced


class StalenessVerdict(BaseModel):
    """Has the code a memory describes moved on since the memory was written?

    Three-way, not two: a changed file with a newer memory covering the same
    anchors is not stale-and-dangerous, it is *superseded* (``stale``); a changed
    file with nothing newer is the dangerous case (``gap``) — the code moved,
    nobody recorded why, and this memory is now the most recent thing known.
    """

    status: Literal["fresh", "stale", "gap"]
    memoryCommitSha: str  # baseline the check ran against ("" if the memory had none)
    currentShaCheckedAt: str  # ISO timestamp of the check
    changedFiles: list[str] = Field(default_factory=list)  # anchored files that moved
    commitsSince: int | None = None  # distinct commits touching them; None if unknown
    newerMemoryExists: bool = False  # a later memory covers the same anchors


class EntryPoint(BaseModel):
    """A graph node a semantic query lands on — where an agent walk begins.

    The `type`/`isCommunityReport` tags are what let the caller route: strategy
    is picked by what you entered on (a raw decision vs. a community summary).
    """

    nodeId: str  # deterministic graph id, e.g. "dec:<memoryIndex._id>"
    type: NodeType  # always "decision" today (entry is always a memory node)
    label: str  # short_label(contentSnapshot)
    score: float | None = None  # Backboard similarity when the hit carries one
    # Community reports (summary nodes) are a later ticket; until they exist an
    # entry point is always a raw decision, never a report. See [[T4b]].
    isCommunityReport: bool = False


class WalkNeighbor(BaseModel):
    """One node adjacent to a walk's origin, reached over a specific edge kind.

    Decision-typed neighbors carry the fields agents reason over (snapshot,
    staleness, confidence); non-decision neighbors leave them None.
    """

    nodeId: str
    type: NodeType
    label: str
    contentSnapshot: str | None = None  # decision neighbors only
    stalenessStatus: Literal["fresh", "stale", "gap"] | None = None  # decisions
    confidence: MemoryConfidence | None = None  # decisions
    prNumber: int | None = None
    feature: str | None = None
    symbols: list[str] | None = None  # governs edges: symbols ride the edge
    # A community report covering this node would let the agent zoom out to the
    # summary instead of only sideways to peers. Later ticket; False for now.
    hasCommunityReport: bool = False
    # Populated only at walk depth == 2: this neighbor's own neighbors, grouped
    # by edge kind, with the walk origin excluded (no trivial backtrack edges).
    neighbors: dict[WalkEdgeKind, list["WalkNeighbor"]] | None = None


class GraphWalk(BaseModel):
    """A directed local traversal from one node: neighbors grouped by edge kind.

    Grouping (not a flat list) is deliberate — the LLM reasons far better over
    "the 2 decisions this supersedes and the 4 files it governs" than over a
    dozen mixed edges. Only non-empty groups appear.
    """

    nodeId: str
    type: NodeType
    label: str
    contentSnapshot: str | None = None  # set when the origin is a decision
    hasCommunityReport: bool = False  # later ticket (T4b): zoom-out signal
    neighbors: dict[WalkEdgeKind, list[WalkNeighbor]] = Field(default_factory=dict)
    truncated: bool = False  # a per-edge-kind cap was hit — some neighbors omitted
