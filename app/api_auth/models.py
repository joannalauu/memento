from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pymongo import IndexModel
from sqlmodel import Field

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


@register_document
class ApiKey(Document):
    """Auth for BOTH the MCP server and the Claude Code hook plugin.

    Only the SHA-256 hash of the key is stored (`keyHash`); the raw key is shown
    to the engineer once at creation and never persisted. On ingest the incoming
    key is hashed and looked up here to resolve `userId` + `orgId`.
    """

    userId: PydanticObjectId
    orgId: PydanticObjectId
    keyHash: str
    label: str
    lastUsedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "apiKeys"
        indexes = [
            IndexModel("keyHash", name="key_hash_unique", unique=True),
        ]
