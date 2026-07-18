"""Shapes for T3.2 session↔PR distillation.

`DistillationOutput` is exactly what the model must emit (and is parsed
tolerantly, element by element, like app/file_upload/enrichment.py's claims);
`DistillationResult` is what the pipeline persists onto the PipelineJob for
T3.3 to consume — the model output plus the provenance T3.3 needs (which
sessions fed it, how they were matched, what was dropped for budget).
"""

from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.context_engine.schemas import ConsistencyConflict, StalenessVerdict

DecisionConfidence = Literal["high", "medium", "low"]
MatchMode = Literal["branch", "author_recent"]


class DecisionAnchors(BaseModel):
    """Per-decision anchors — `Anchors` minus repo, which is job-level."""

    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


class DistilledDecision(BaseModel):
    """One durable decision record extracted from the session transcripts."""

    content: str
    anchors: DecisionAnchors = Field(default_factory=DecisionAnchors)
    feature: str  # slug; matched to the org's Features or newly coined
    confidence: DecisionConfidence
    # Set by T3.3 after this decision is written to Backboard; its presence marks
    # the decision as already committed so a resumed write skips it (no dup).
    bbMemoryId: str | None = None


class DistillationOutput(BaseModel):
    """The model's structured answer. Empty `decisions` is a valid answer."""

    decisions: list[DistilledDecision] = Field(default_factory=list)
    conflicts: list[ConsistencyConflict] = Field(default_factory=list)


class DistillationResult(BaseModel):
    """Persisted on PipelineJob.result (as a plain dict) for T3.3."""

    decisions: list[DistilledDecision] = Field(default_factory=list)
    conflicts: list[ConsistencyConflict] = Field(default_factory=list)
    sessionIds: list[PydanticObjectId] = Field(default_factory=list)
    droppedSessionIds: list[PydanticObjectId] = Field(default_factory=list)
    matchMode: MatchMode
    # The merged PR's head sha — T3.3 stamps it onto every memory it writes so
    # a later staleness_check has a baseline to diff the code against.
    commitSha: str
    distilledAt: datetime


class StaleMemoryFlag(BaseModel):
    """A pre-existing memory on files this PR changed, flagged when no session
    was captured for the change — raised on the coverage-gap branch so the
    gap is visible (which prior rationale may now be out of date) not silent.
    """

    bbMemoryId: str
    verdict: StalenessVerdict
