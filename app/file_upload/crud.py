from typing import Literal

from beanie import PydanticObjectId

from app.file_upload.models import DocumentIndexEntry

# Backboard's DocumentStatus values map 1:1 onto our index literals except for
# "failed", which we record as "error".
_STATUS_MAP: dict[str, Literal["pending", "processing", "indexed", "error"]] = {
    "pending": "pending",
    "processing": "processing",
    "indexed": "indexed",
    "failed": "error",
}


def normalize_document_status(
    status: object,
) -> Literal["pending", "processing", "indexed", "error"]:
    """Map a Backboard DocumentStatus (enum or raw string) onto the index
    literal, defaulting to "pending" for anything unrecognized."""
    value = getattr(status, "value", status)
    return _STATUS_MAP.get(str(value), "pending")


async def get_document_index_entry(
    *, org_id: PydanticObjectId, doc_id: PydanticObjectId
) -> DocumentIndexEntry | None:
    """Retrieve an org's document-index entry by its ObjectId, scoped to the
    org so one org can't read another's document ids."""
    return await DocumentIndexEntry.find_one(
        DocumentIndexEntry.id == doc_id, DocumentIndexEntry.orgId == org_id
    )


async def list_document_index_entries(
    org_id: PydanticObjectId,
) -> list[DocumentIndexEntry]:
    """List an org's document-index entries, newest first."""
    return (
        await DocumentIndexEntry.find(DocumentIndexEntry.orgId == org_id)
        .sort(-DocumentIndexEntry.createdAt)
        .to_list()
    )


async def update_document_status(
    entry: DocumentIndexEntry, status: object
) -> DocumentIndexEntry:
    """Refresh an entry's status from a Backboard document status, persisting
    only when it actually changed. Writes just the ``status`` field (atomic
    ``$set``) so a concurrent enrichment-status update isn't clobbered."""
    normalized = normalize_document_status(status)
    if entry.status != normalized:
        entry.status = normalized
        await entry.set({DocumentIndexEntry.status: normalized})
    return entry


async def set_document_enrichment_status(
    doc_id: PydanticObjectId,
    status: Literal["none", "enriching", "done", "failed"],
    *,
    decisions_written: int | None = None,
    gaps_opened: int | None = None,
) -> None:
    """Persist a document's enrichment/gap-detection phase, addressing it by id
    and writing just those fields so a concurrent indexing-status refresh isn't
    clobbered. Pass the counts when moving to "done" to record the outcome.
    No-ops if the entry was deleted mid-enrichment."""
    entry = await DocumentIndexEntry.get(doc_id)
    if entry is None:
        return
    updates: dict[object, object] = {DocumentIndexEntry.enrichmentStatus: status}
    if decisions_written is not None:
        updates[DocumentIndexEntry.decisionsWritten] = decisions_written
    if gaps_opened is not None:
        updates[DocumentIndexEntry.gapsOpened] = gaps_opened
    await entry.set(updates)


async def delete_document_index_entry(entry: DocumentIndexEntry) -> None:
    """Remove a document-index entry."""
    await entry.delete()


async def create_document_index_entry(
    *,
    org_id: PydanticObjectId,
    bb_document_id: str,
    filename: str,
    status: object,
    kind: Literal["upload", "decision_digest"] = "upload",
    repo_id: PydanticObjectId | None = None,
    enrichment_status: Literal["none", "enriching", "done", "failed"] = "none",
) -> DocumentIndexEntry:
    """Record an uploaded Backboard document in the org's document index. Pass
    ``enrichment_status="enriching"`` when a background enrichment job is being
    kicked off for this doc, so the phase is visible before the job reports."""
    entry = DocumentIndexEntry(
        orgId=org_id,
        repoId=repo_id,
        bbDocumentId=bb_document_id,
        filename=filename,
        kind=kind,
        status=normalize_document_status(status),
        enrichmentStatus=enrichment_status,
    )
    await entry.insert()
    return entry
