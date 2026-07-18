from datetime import datetime, timezone

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.gap_chat.models import GapChat, GapChatStatus, GapMessage


async def get_open_chat_for_memory(bb_memory_id: str) -> GapChat | None:
    """The live (open) chat reconciling a given memory, if one exists."""
    return await GapChat.find_one(
        GapChat.bbMemoryId == bb_memory_id, GapChat.status == "open"
    )


async def create_gap_chat(
    *,
    org_id: PydanticObjectId,
    repo_id: PydanticObjectId,
    bb_memory_id: str,
    memory_content: str,
    trigger_commit_sha: str,
    trigger_status: str,
    changed_files: list[str],
    pr_number: int | None,
    question: str,
    bb_thread_id: str | None,
) -> GapChat:
    """Open a gap chat seeded with the assistant's verification question.

    Idempotent per memory: the partial-unique index means a second open attempt
    collapses onto the existing conversation rather than raising."""
    chat = GapChat(
        orgId=org_id,
        repoId=repo_id,
        bbMemoryId=bb_memory_id,
        bbThreadId=bb_thread_id,
        memoryContent=memory_content,
        changedFiles=changed_files,
        prNumber=pr_number,
        triggerCommitSha=trigger_commit_sha,
        triggerStatus=trigger_status,
        messages=[GapMessage(role="assistant", text=question)],
    )
    try:
        await chat.insert()
    except DuplicateKeyError:
        existing = await get_open_chat_for_memory(bb_memory_id)
        if existing is not None:
            return existing
        raise
    return chat


async def get_gap_chat(
    org_id: PydanticObjectId, chat_id: PydanticObjectId
) -> GapChat | None:
    """Fetch one chat scoped to its org (so an org can't read another's)."""
    return await GapChat.find_one(GapChat.id == chat_id, GapChat.orgId == org_id)


async def list_gap_chats(
    org_id: PydanticObjectId, status: GapChatStatus | None = None
) -> list[GapChat]:
    """An org's gap chats, newest first; filter by status when given."""
    query: dict = {"orgId": org_id}
    if status is not None:
        query["status"] = status
    return await GapChat.find(query).sort(-GapChat.createdAt).to_list()


async def append_message(chat: GapChat, role: str, text: str) -> GapChat:
    """Append one turn and bump ``updatedAt``."""
    chat.messages.append(GapMessage(role=role, text=text))
    chat.updatedAt = datetime.now(timezone.utc)
    await chat.save()
    return chat
