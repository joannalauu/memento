from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    """A document indexed against an org's Backboard assistant.

    ``chunkCount``/``totalTokens`` are populated only once ``status`` is
    ``indexed``; ``error``/``recommendation`` only when ``status`` is
    ``error``. All four are live values from Backboard, not persisted."""

    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    orgId: PydanticObjectId
    repoId: PydanticObjectId | None = None
    bbDocumentId: str
    filename: str
    kind: Literal["upload", "decision_digest"]
    status: Literal["pending", "processing", "indexed", "error"]
    # Background enrichment phase, distinct from the indexing `status` above —
    # see app/file_upload/models.py.
    enrichmentStatus: Literal["none", "enriching", "done", "failed"] = "none"
    # Enrichment outcome (meaningful once enrichmentStatus == "done").
    decisionsWritten: int = 0
    createdAt: datetime

    # Surfaced when status == "indexed".
    chunkCount: int | None = None
    totalTokens: int | None = None

    # Surfaced when status == "error".
    error: str | None = None
    recommendation: str | None = None

    @classmethod
    def from_entry(cls, entry: object, bb_document: object) -> "DocumentRead":
        """Build a response from a persisted index entry, layering in the
        step-specific live fields from a Backboard document status."""
        doc = cls.model_validate(entry)
        if doc.status == "indexed":
            doc.chunkCount = getattr(bb_document, "chunk_count", None)
            doc.totalTokens = getattr(bb_document, "total_tokens", None)
        elif doc.status == "error":
            doc.error = (
                getattr(bb_document, "status_message", None)
                or "Document indexing failed."
            )
            doc.recommendation = "Delete this document and upload it again."
        return doc
