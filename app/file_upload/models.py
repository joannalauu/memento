from datetime import datetime, timezone
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


@register_document
class DocumentIndexEntry(Document):
    """Manual legacy-doc uploads."""

    orgId: PydanticObjectId
    repoId: PydanticObjectId | None = None
    bbDocumentId: str
    filename: str
    kind: Literal["upload", "decision_digest"]
    status: Literal["pending", "processing", "indexed", "error"]
    # Tracks the background enrichment + gap-detection phase, which is entirely
    # separate from Backboard's indexing `status` above (a doc reads "indexed"
    # the whole time enrichment is still running). "none" for docs that aren't
    # repo-scoped and so never enrich. See app/file_upload/enrichment.py.
    enrichmentStatus: Literal["none", "enriching", "done", "failed"] = "none"
    # Enrichment outcome, filled when enrichmentStatus reaches "done": how many
    # decision memories the doc produced and how many became gap-review questions.
    decisionsWritten: int = 0
    gapsOpened: int = 0
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "documentIndex"
        indexes = [
            IndexModel("bbDocumentId", name="bb_document_id_unique", unique=True),
            IndexModel([("orgId", 1), ("kind", 1)], name="org_kind"),
        ]
