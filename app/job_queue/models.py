from datetime import datetime, timezone
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document


@register_document
class PipelineJob(Document):
    """Merged-PR pipeline work item.

    Enqueued by the GitHub webhook (app/github/routes.py) when a PR merges;
    consumed by a follow-up worker that fetches PR files, matches AgentSessions
    by repoId + headBranch, distills via Backboard, and writes memories.
    status/attempts/error are the worker's seam — nothing consumes jobs yet.
    """

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    prNumber: int
    headSha: str
    headBranch: str  # PR head ref — matches AgentSession.branch
    baseBranch: str
    authorUserId: PydanticObjectId | None = None  # None when GitHub login unlinked
    prAuthorGithub: str  # raw login, lowercased; fallback identity
    deliveryId: str  # provenance only; dedupe is repo_pr_sha_unique
    installationId: int
    prTitle: str | None = None
    prUrl: str | None = None
    mergedAt: datetime | None = None
    status: Literal["queued", "running", "done", "failed"] = "queued"
    attempts: int = 0
    error: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "pipelineJobs"
        indexes = [
            # the work's semantic identity — a redelivery (fresh deliveryId)
            # for the same merge collapses onto the existing job
            IndexModel(
                [("repoId", 1), ("prNumber", 1), ("headSha", 1)],
                name="repo_pr_sha_unique",
                unique=True,
            ),
            # worker poll: oldest queued first
            IndexModel([("status", 1), ("createdAt", 1)], name="status_created"),
        ]
