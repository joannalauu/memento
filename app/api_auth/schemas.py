from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # Optional: required only when the caller belongs to more than one org.
    orgId: PydanticObjectId | None = None


class ApiKeyCreated(BaseModel):
    """Response for key creation. `key` is the raw secret, returned exactly once."""

    id: PydanticObjectId
    label: str
    orgId: PydanticObjectId
    key: str
    createdAt: datetime


class ApiKeyRead(BaseModel):
    """Response for reading a key. The secret is never included — only its hash
    is stored server-side, and even that is withheld here."""

    id: PydanticObjectId
    label: str
    orgId: PydanticObjectId
    lastUsedAt: datetime | None = None
    createdAt: datetime
