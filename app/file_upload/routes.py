import os
import shutil
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from backboard.exceptions import BackboardNotFoundError

from app.backboard.client import Backboard, get_backboard
from app.dependencies import get_current_user
from app.file_upload.crud import (
    create_document_index_entry,
    delete_document_index_entry,
    get_document_index_entry,
    list_document_index_entries,
    update_document_status,
)
from app.file_upload.enrichment import run_document_enrichment
from app.file_upload.models import DocumentIndexEntry
from app.file_upload.schemas import DocumentRead
from app.file_upload.text_extract import extract_document_text
from app.github.client import GitHubApp, get_github
from app.orgs.crud import get_org
from app.orgs.models import Org, Repo, User

router = APIRouter()


async def _sync_document(
    backboard: Backboard, entry: DocumentIndexEntry
) -> DocumentRead:
    """Refresh a single entry's status from Backboard and build its response.
    Raises on any Backboard error — callers that must tolerate per-document
    failures should handle it (see the list endpoint)."""
    bb_document = await backboard.get_document_status(entry.bbDocumentId)
    entry = await update_document_status(entry, bb_document.status)
    return DocumentRead.from_entry(entry, bb_document)


@router.post(
    "/{org_id}", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
async def upload_org_document_endpoint(
    org_id: PydanticObjectId,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    repo_id: PydanticObjectId | None = Form(None),
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
    github: GitHubApp = Depends(get_github),
) -> DocumentRead:
    """Upload a document to the org's Backboard assistant and record it in the
    org's document index. Only a member of the org may upload.

    Pass ``repo_id`` to scope a legacy doc to a repo: after upload, a background
    anchor-enrichment job extracts the doc's decision-like claims, grounds them
    against the repo tree, and writes each as a ``legacy_doc`` memory so the
    knowledge is reachable through anchor-based search (see enrichment.py). The
    full doc always lands in RAG for narrative answers, repo or not."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    # Optional repo scope for anchor enrichment. Resolve within the org so one
    # org can't tag a doc against another's repo.
    repo: Repo | None = None
    if repo_id is not None:
        repo = await Repo.find_one(Repo.id == repo_id, Repo.orgId == org_id)
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repo not found in this org",
            )

    # The Backboard SDK uploads from a path, so spool the incoming stream to a
    # temp file first. Preserve the original suffix so Backboard can infer the
    # document type. Clean up the temp file regardless of outcome.
    suffix = Path(file.filename or "").suffix
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    # Capture the text now (only when enriching): the temp file is gone by the
    # time the background task runs, and Backboard exposes no way to read it back.
    doc_text = ""
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            shutil.copyfileobj(file.file, tmp)
        if repo is not None:
            doc_text = extract_document_text(tmp_path, file.filename)
        bb_document = await backboard.upload_document_to_assistant(
            org.bbAssistantId, tmp_path
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    entry = await create_document_index_entry(
        org_id=org_id,
        repo_id=repo.id if repo is not None else None,
        bb_document_id=str(bb_document.document_id),
        filename=file.filename or bb_document.filename,
        status=bb_document.status,
        # A repo-scoped doc kicks off enrichment + gap detection below; flag the
        # phase now so clients can distinguish "indexed" from "done analyzing".
        enrichment_status="enriching" if repo is not None else "none",
    )
    if repo is not None:
        background_tasks.add_task(
            run_document_enrichment,
            entry,
            doc_text=doc_text,
            org=org,
            repo=repo,
            bb=backboard,
            github=github,
        )
    return entry


@router.get("/{org_id}", response_model=list[DocumentRead])
async def list_org_documents_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> list[DocumentRead]:
    """List an org's documents, newest first, merging each one's live status
    from Backboard so the returned steps are current. Statuses come from a
    single ``list_assistant_documents`` call rather than one call per document.
    A document missing from Backboard's response falls back to its last-known
    persisted status. Only a member of the org may view them."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    entries = await list_document_index_entries(org_id)
    # One Backboard call for the whole assistant, indexed by document id, then
    # merged onto the local mirror entries.
    bb_documents = await backboard.list_assistant_documents(org.bbAssistantId)
    bb_by_id = {str(doc.document_id): doc for doc in bb_documents}

    results: list[DocumentRead] = []
    for entry in entries:
        bb_document = bb_by_id.get(entry.bbDocumentId)
        if bb_document is None:
            # Not in Backboard's list — surface the last-known status rather
            # than dropping the document.
            results.append(DocumentRead.model_validate(entry))
            continue
        entry = await update_document_status(entry, bb_document.status)
        results.append(DocumentRead.from_entry(entry, bb_document))
    return results


@router.get("/{org_id}/{doc_id}", response_model=DocumentRead)
async def get_org_document_endpoint(
    org_id: PydanticObjectId,
    doc_id: PydanticObjectId,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> DocumentRead:
    """Retrieve an org document, refreshing its indexing status from Backboard
    to reflect the current processing step. Only a member of the org may view
    it."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    entry = await get_document_index_entry(org_id=org_id, doc_id=doc_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    return await _sync_document(backboard, entry)


@router.delete("/{org_id}/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_document_endpoint(
    org_id: PydanticObjectId,
    doc_id: PydanticObjectId,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> None:
    """Delete an org document from the Backboard assistant and remove its index
    entry. Only a member of the org may delete it."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    entry = await get_document_index_entry(org_id=org_id, doc_id=doc_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    # Delete from Backboard first; only drop the index entry once the source of
    # truth is gone, so a Backboard failure leaves a recoverable record rather
    # than an orphaned document with no local handle. A 404 means it's already
    # gone on Backboard's side — treat that as success (idempotent) and still
    # clear the stale local entry. Other errors (auth, 5xx, rate limit)
    # propagate.
    try:
        await backboard.delete_document(entry.bbDocumentId)
    except BackboardNotFoundError:
        pass
    await delete_document_index_entry(entry)
