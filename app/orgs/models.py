from datetime import datetime, timezone
from typing import Annotated

from beanie import Indexed
from pydantic import Field, field_validator
from pymongo import IndexModel

from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401
from app.hackplate.user.models import AbstractUserDocument


@register_document
class User(AbstractUserDocument):
    name: str | None = None
    role: str | None = None

    # unique + sparse: lets multiple users have no githubUsername (email/password
    # or google-only accounts) without tripping the unique constraint on None
    githubUsername: Annotated[str | None, Indexed(unique=True, sparse=True)] = None

    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("email", "githubUsername", mode="before")
    @classmethod
    def _lowercase(cls, v: str | None) -> str | None:
        return v.lower() if isinstance(v, str) else v

    class Settings(AbstractUserDocument.Settings):
        indexes = AbstractUserDocument.Settings.indexes + [
            IndexModel(
                "githubUsername",
                name="github_username_unique",
                unique=True,
                sparse=True,
            ),
        ]
