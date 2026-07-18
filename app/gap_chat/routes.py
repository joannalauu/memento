import os
import shutil
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)

from app.backboard.client import Backboard, get_backboard
from app.dependencies import get_current_user
from app.gap_chat import crud, service
from app.gap_chat.models import GapChat, GapChatStatus
from app.gap_chat.schemas import AnswerRequest, AnswerResult, GapChatRead
from app.orgs.crud import get_org
from app.orgs.models import Org, User

router = APIRouter()


def _answer_result(chat: GapChat, transcript: str | None = None) -> AnswerResult:
    """Map a post-answer chat to the response, raising for the non-resolving
    outcomes: still-open (classification failed) or dismissed (memory gone)."""
    if chat.status == "open":
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Could not classify the answer; please retry.",
        )
    if chat.status == "dismissed":
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="The memory under review is no longer active.",
        )
    return AnswerResult(
        chat=GapChatRead.model_validate(chat),
        resolution="verified" if chat.status == "verified" else "superseded",
        supersededByMemoryId=chat.supersededByMemoryId,
        transcript=transcript,
    )


async def _require_member(org_id: PydanticObjectId, user: User) -> Org:
    org = await get_org(org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Org not found")
    if not any(m.userId == user.id for m in org.members):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Not a member of this org"
        )
    return org


@router.get("/{org_id}", response_model=list[GapChatRead])
async def list_gap_chats_endpoint(
    org_id: PydanticObjectId,
    status_filter: GapChatStatus | None = None,
    user: User = Depends(get_current_user),
) -> list[GapChatRead]:
    """List an org's gap chats (optionally by status), newest first. Only a
    member may view them."""
    await _require_member(org_id, user)
    chats = await crud.list_gap_chats(org_id, status_filter)
    return [GapChatRead.model_validate(c) for c in chats]


@router.get("/{org_id}/{chat_id}", response_model=GapChatRead)
async def get_gap_chat_endpoint(
    org_id: PydanticObjectId,
    chat_id: PydanticObjectId,
    user: User = Depends(get_current_user),
) -> GapChatRead:
    await _require_member(org_id, user)
    chat = await crud.get_gap_chat(org_id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Gap chat not found")
    return GapChatRead.model_validate(chat)


@router.post("/{org_id}/{chat_id}/answer", response_model=AnswerResult)
async def answer_gap_chat_endpoint(
    org_id: PydanticObjectId,
    chat_id: PydanticObjectId,
    payload: AnswerRequest,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> AnswerResult:
    """Answer the verification question, closing the gap between the legacy
    memory and the code: the memory is upgraded to verified or superseded."""
    org = await _require_member(org_id, user)
    chat = await crud.get_gap_chat(org_id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Gap chat not found")
    if chat.status != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Gap chat is already resolved"
        )

    chat = await service.submit_answer(
        chat, payload.answer, org=org, bb=backboard, author_user_id=user.id
    )
    return _answer_result(chat)


@router.post("/{org_id}/{chat_id}/answer/audio", response_model=AnswerResult)
async def answer_gap_chat_audio_endpoint(
    org_id: PydanticObjectId,
    chat_id: PydanticObjectId,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> AnswerResult:
    """Answer the verification question by voice: the uploaded audio is
    transcribed (ElevenLabs speech-to-text via Backboard) and the transcript runs
    through the same verify/supersede flow as a typed answer."""
    org = await _require_member(org_id, user)
    chat = await crud.get_gap_chat(org_id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Gap chat not found")
    if chat.status != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Gap chat is already resolved"
        )

    # The SDK transcribes from a path, so spool the upload to a temp file first,
    # preserving the suffix so the STT provider can infer the audio format.
    suffix = Path(file.filename or "").suffix
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            shutil.copyfileobj(file.file, tmp)
        chat, transcript = await service.submit_audio_answer(
            chat, tmp_path, org=org, bb=backboard, author_user_id=user.id
        )
    except service.TranscriptionError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return _answer_result(chat, transcript=transcript)
