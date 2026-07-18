from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict

from app.gap_chat.models import GapChatStatus

GapResolution = Literal["verified", "superseded"]


class GapClassification(BaseModel):
    """The chatbot's read of an answer: does the legacy claim still hold
    (``verified``) or has the code moved past it (``superseded``)? For a
    supersession, ``statement`` is the corrected, now-accurate decision."""

    resolution: GapResolution
    statement: str | None = None
    reasoning: str = ""


class GapMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: Literal["assistant", "user"]
    text: str
    createdAt: datetime


class GapChatRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    orgId: PydanticObjectId
    repoId: PydanticObjectId
    bbMemoryId: str
    memoryContent: str
    changedFiles: list[str]
    prNumber: int | None
    triggerStatus: Literal["stale", "gap"]
    messages: list[GapMessageRead]
    status: GapChatStatus
    supersededByMemoryId: str | None
    resolvedAt: datetime | None
    createdAt: datetime


class AnswerRequest(BaseModel):
    answer: str


class AnswerResult(BaseModel):
    """Outcome of answering: the resolved chat plus what happened to the memory."""

    chat: GapChatRead
    resolution: GapResolution
    supersededByMemoryId: str | None = None
