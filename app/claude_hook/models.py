from datetime import datetime, timezone
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.backboard.models import Anchors
from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


class Question(BaseModel):
    id: str
    text: str
    reason: Literal[
        "new_pattern", "new_dependency", "conflict", "legacy_verification", "general"
    ]
    relatedMemoryIds: list[str] | None = None
    relatedFiles: list[str] | None = None


class Answer(BaseModel):
    questionId: str
    answer: str
    answeredAt: datetime


class Conflict(BaseModel):
    bbMemoryId: str
    summary: str
    priorPr: int | None = None


class Coverage(BaseModel):
    autoAnswered: int
    total: int
    sessionIds: list[PydanticObjectId]


@register_document
class AgentSession(Document):
    """
    Claude Code chat history — raw capture. Raw transcripts live HERE, never
    in Backboard. Only distilled decision records become memories.
    """

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    userId: PydanticObjectId  # resolved from the API key on ingest
    sessionId: str  # Claude Code session_id from hook stdin
    branch: str
    transcriptRef: str  # GridFS id or object-store key (raw JSONL)
    normalizedRef: str | None = None  # set by the normalizer (signal-only version)
    tokenEstimate: int | None = None
    status: Literal["stored", "normalized", "distilled", "expired"]
    prNumber: int | None = None  # set when matched to a PR
    expiresAt: datetime | None = (
        None  # set on ingest (+14d); unset when matched/distilled
    )
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "agentSessions"
        indexes = [
            # hook re-fires -> E11000 -> skip
            IndexModel("sessionId", name="session_id_unique", unique=True),
            # PR-time matching
            IndexModel(
                [("repoId", 1), ("branch", 1), ("status", 1)],
                name="repo_branch_status",
            ),
            # TTL only reaps docs where expiresAt is set; matched sessions
            # have it unset, so they persist
            IndexModel("expiresAt", name="expires_at_ttl", expireAfterSeconds=0),
        ]


@register_document
class ContextRequest(Document):
    """FALLBACK question form — not a chat."""

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    prNumber: int
    prTitle: str
    prUrl: str
    prAuthorGithub: str
    assigneeUserId: PydanticObjectId | None = None
    headSha: str
    complexityScore: int  # drives question count (2-6)
    questions: list[Question] = []  # ONLY residual questions if sessions covered some
    answers: list[Answer] = []  # form submission, not a conversation
    anchors: Anchors
    conflicts: list[Conflict] = []
    coverage: Coverage | None = None  # how much the Claude session already answered
    magicToken: str  # email deep link to the form
    status: Literal["pending", "completed", "archived", "expired", "skipped"]
    emailedAt: datetime | None = None
    completedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "contextRequests"
        indexes = [
            IndexModel(
                [("repoId", 1), ("prNumber", 1), ("headSha", 1)],
                name="repo_pr_sha_unique",
                unique=True,
            ),
            IndexModel("magicToken", name="magic_token_unique", unique=True),
            IndexModel([("assigneeUserId", 1), ("status", 1)], name="assignee_status"),
        ]


@register_document
class WebhookEvent(Document):
    deliveryId: str  # X-GitHub-Delivery
    eventType: str  # e.g. "pull_request"
    payload: dict
    status: Literal["received", "processed", "failed", "skipped"]
    processedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "webhookEvents"
        indexes = [
            IndexModel("deliveryId", name="delivery_id_unique", unique=True),
            IndexModel("createdAt", name="created_at_ttl", expireAfterSeconds=2592000),
        ]
