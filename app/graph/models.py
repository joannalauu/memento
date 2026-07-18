from datetime import datetime
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


class Anchors(BaseModel):
    repo: str  # REQUIRED — org-level assistant needs repo scoping
    files: list[str] = []
    symbols: list[str] = []


@register_document
class MemoryIndexEntry(Document):
    """
    Structural mirror = the knowledge graph. Backboard owns memory content +
    semantic retrieval. This collection owns STRUCTURE: it is the node/edge
    source for the graph UI and for agent graph-hopping. contentSnapshot
    caches the content string our backend authored anyway, so hops render
    instantly without a Backboard round trip.
    """

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    bbMemoryId: str
    contentSnapshot: str  # cache of our own authored decision record
    source: Literal["claude_session", "form", "legacy_doc", "manual", "consolidated"]
    confidence: Literal["verified", "unverified"]
    feature: str | None = None  # -> features.name; Feature node edge
    prNumber: int | None = None  # -> PR node edge ("introduced")
    commitSha: str | None = None
    authorUserId: PydanticObjectId | None = None  # -> Engineer node edge ("made")
    anchors: Anchors  # -> File/Symbol node edges ("governs")
    supersededBy: PydanticObjectId | None = None  # -> Decision->Decision edge
    mergedFrom: list[PydanticObjectId] | None = None  # consolidation lineage
    archivedContent: str | None = None
    deletedAt: datetime | None = None
    createdAt: datetime

    class Settings:
        name = "memoryIndex"
        indexes = [
            IndexModel("bbMemoryId", name="bb_memory_id_unique", unique=True),
            IndexModel("anchors.files", name="anchors_files"),  # multikey
            IndexModel("anchors.symbols", name="anchors_symbols"),  # multikey
            IndexModel(
                [("orgId", 1), ("feature", 1)], name="org_feature"
            ),  # feature clusters
            IndexModel(
                [("repoId", 1)],
                name="active_repo_partial",
                partialFilterExpression={"deletedAt": None},
            ),  # active counts (consolidation trigger)
        ]
