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

ConsistencyMode = Literal["audit", "preflight"]


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
