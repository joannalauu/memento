from datetime import datetime, timezone
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401

MemorySource = Literal["claude_session", "form", "legacy_doc", "manual", "consolidated"]
MemoryConfidence = Literal["verified", "unverified"]


class Anchors(BaseModel):
    """Code locations a memory governs — source of File/Symbol graph edges."""

    repo: str
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


@register_document
class MemoryIndex(Document):
    """
    Structural mirror of a Backboard memory (the knowledge graph).

    Backboard owns memory content + semantic retrieval. This collection owns
    STRUCTURE: it is the node/edge source for the graph UI and for agent
    graph-hopping. contentSnapshot caches the content string our backend
    authored anyway, so hops render instantly without a Backboard round trip.
    """

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    bbMemoryId: str
    contentSnapshot: str
    source: MemorySource = "manual"
    confidence: MemoryConfidence = "unverified"
    feature: str | None = None  # -> features.name; Feature node edge
    prNumber: int | None = None  # -> PR node edge ("introduced")
    commitSha: str | None = None
    authorUserId: PydanticObjectId | None = None  # -> Engineer node edge ("made")
    anchors: Anchors  # -> File/Symbol node edges ("governs")
    supersededBy: PydanticObjectId | None = None  # Decision->Decision evolution chain
    mergedFrom: list[PydanticObjectId] | None = None  # consolidation lineage
    archivedContent: str | None = None
    deletedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "memoryIndex"
        indexes = [
            IndexModel("bbMemoryId", name="bb_memory_id_unique", unique=True),
            IndexModel("anchors.files", name="anchors_files"),
            IndexModel("anchors.symbols", name="anchors_symbols"),
            IndexModel([("orgId", 1), ("feature", 1)], name="org_feature"),
            # active-memory counts per repo (consolidation trigger)
            IndexModel(
                "repoId",
                name="active_repo_partial",
                partialFilterExpression={"deletedAt": None},
            ),
        ]
