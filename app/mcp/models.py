from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


@register_document
class ApiKey(Document):
    """Auth for BOTH the MCP server and the Claude Code hook plugin."""

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
