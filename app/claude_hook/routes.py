from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from pymongo.asynchronous.database import AsyncDatabase

from app.claude_hook import crud
from app.claude_hook.schemas import AgentSessionIngestAccepted
from app.dependencies import ApiKeyPrincipal, get_api_key_principal, get_client

router = APIRouter()

# Shared detail for every repo-resolution failure. The hook drops the transcript
# silently on any non-2xx, so there's nothing to gain from distinguishing a
# missing header from an unregistered repo.
_UNKNOWN_REPO = "unknown repository"


@router.post(
    "/agent-sessions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AgentSessionIngestAccepted,
)
async def ingest_agent_session(
    request: Request,
    background_tasks: BackgroundTasks,
    x_session_id: Annotated[str, Header()],
    x_git_branch: Annotated[str | None, Header()] = None,
    x_git_remote: Annotated[str | None, Header()] = None,
    x_token_estimate: Annotated[int | None, Header()] = None,
    x_hook_version: Annotated[str | None, Header()] = None,
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
    db: AsyncDatabase = Depends(get_client),
) -> AgentSessionIngestAccepted:
    """Ingest a Claude Code session transcript from the @memento/hook client.

    Auth is by API key (Bearer). The repo is resolved from X-Git-Remote within
    the key's org. The gzipped JSONL body is decompressed and stored in GridFS,
    and an AgentSession is upserted on sessionId (a resumed session replaces its
    prior capture). Responds 202 and hands the session to the normalizer.

    Blob-safety ordering is upload-new → write-doc → delete-old, so a failure
    leaves at worst an orphaned blob, never a transcriptRef pointing at nothing.
    """
    # Cheapest rejection first: resolve the repo before touching the body.
    if x_git_remote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_UNKNOWN_REPO)
    parsed = crud.parse_git_remote(x_git_remote)
    if parsed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_UNKNOWN_REPO)
    owner, name = parsed
    repo = await crud.find_repo_by_remote(principal.api_key.orgId, owner, name)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_UNKNOWN_REPO)
    assert repo.id is not None

    # Reject an oversized body before buffering it, when the client declares a size.
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > crud.MAX_COMPRESSED_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    body = await request.body()
    try:
        raw = crud.gunzip_bounded(body)
    except crud.TooLarge:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    except crud.BadGzip:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="body must be gzip-compressed JSONL",
        )
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty transcript")

    # Upload the new blob first so the doc never points at a nonexistent file.
    new_ref = await crud.upload_transcript(db, x_session_id, raw)
    try:
        doc, old_refs = await crud.upsert_agent_session(
            org_id=principal.api_key.orgId,
            repo_id=repo.id,
            user_id=principal.api_key.userId,
            session_id=x_session_id,
            branch=x_git_branch or "",
            transcript_ref=new_ref,
            token_estimate=x_token_estimate,
        )
    except Exception:
        # Don't orphan the fresh blob if the doc write fails.
        await crud.delete_transcript(db, new_ref)
        raise

    # GC superseded blobs (old transcript + old normalized version) only after
    # the doc write succeeds.
    for old_ref in old_refs:
        if old_ref and old_ref != new_ref:
            await crud.delete_transcript(db, old_ref)

    background_tasks.add_task(crud.enqueue_normalization, db, str(doc.id))
    return AgentSessionIngestAccepted.model_validate(doc)
