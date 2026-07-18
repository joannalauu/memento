from datetime import datetime, timezone

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401


@register_document
class GitHubInstallState(Document):
    """Short-lived attribution token linking a GitHub App installation redirect
    back to the org (and user) that initiated it.

    GitHub's setup-URL callback tells us the ``installation_id`` but not which
    org it belongs to, and a browser redirect can't carry our bearer token — so
    the ``state`` we mint on ``/connect`` is the proof of both which org to bind
    and that an admin initiated it. Consumed once on the callback; a TTL index
    reaps states that are never redeemed."""

    orgId: PydanticObjectId
    userId: PydanticObjectId
    token: str
    expiresAt: datetime
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "githubInstallStates"
        indexes = [
            IndexModel("token", name="token_unique", unique=True),
            IndexModel("expiresAt", name="expires_at_ttl", expireAfterSeconds=0),
        ]
