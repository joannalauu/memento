from datetime import datetime, timezone
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401

GapChatStatus = Literal["open", "verified", "superseded", "dismissed"]


class GapMessage(BaseModel):
    """One turn in a gap-closing chat. ``assistant`` turns are the chatbot's
    verification question and its closing summary; ``user`` turns are answers."""

    role: Literal["assistant", "user"]
    text: str
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@register_document
class GapChat(Document):
    """A short chat that reconciles one legacy-doc memory with the code as it
    stands now.

    Raised lazily: when a merge touches files a ``legacy_doc`` memory anchors and
    `staleness_check` returns non-fresh, we ask the one question "the old docs say
    X about this area — is that still accurate?". The answer either upgrades the
    memory to ``verified`` (and re-baselines its ``commitSha`` so it stops
    flagging) or supersedes it with a corrected, verified memory. This is the only
    infrastructure the by-interview refresh adds; the conversation itself lives on
    a Backboard thread (``bbThreadId``).
    """

    orgId: PydanticObjectId
    repoId: PydanticObjectId
    bbMemoryId: str  # the stale legacy_doc memory under review
    bbThreadId: str | None = None  # Backboard thread hosting the conversation
    memoryContent: str  # snapshot of the memory ("X") when the chat opened
    changedFiles: list[str] = Field(default_factory=list)  # the area that moved
    prNumber: int | None = None  # where the code is changing, if known
    # The commit sha of the code state that triggered this — the new staleness
    # baseline stamped onto the memory when the answer confirms/supersedes it.
    triggerCommitSha: str
    triggerStatus: Literal["stale", "gap"]  # the verdict that raised the chat
    messages: list[GapMessage] = Field(default_factory=list)
    status: GapChatStatus = "open"
    supersededByMemoryId: str | None = None  # new memory's id, when superseded
    resolvedByUserId: PydanticObjectId | None = None
    resolvedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updatedAt: datetime | None = None

    class Settings:
        name = "gapChats"
        indexes = [
            # At most one OPEN chat per memory — a re-triggered gap collapses onto
            # the existing conversation instead of spawning duplicates.
            IndexModel(
                "bbMemoryId",
                name="open_memory_unique",
                unique=True,
                partialFilterExpression={"status": "open"},
            ),
            # Inbox view: an org's open chats, newest first.
            IndexModel([("orgId", 1), ("status", 1)], name="org_status"),
        ]
