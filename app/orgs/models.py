from datetime import datetime, timezone
from typing import Annotated, Literal

from beanie import Indexed, Document, PydanticObjectId
from pydantic import Field, field_validator, BaseModel
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


class OrgMember(BaseModel):
    userId: PydanticObjectId
    role: Literal["admin", "member"]
    joinedAt: datetime


@register_document
class Org(Document):
    name: str
    slug: str
    githubInstallationId: int | None = None
    bbAssistantId: str
    members: list[OrgMember] = []
    createdAt: datetime

    class Settings:
        name = "orgs"
        indexes = [
            IndexModel("slug", name="slug_unique", unique=True),
            IndexModel("bbAssistantId", name="bb_assistant_id_unique", unique=True),
            IndexModel(
                "githubInstallationId",
                name="github_installation_id_unique",
                unique=True,
                sparse=True,
            ),
            IndexModel("members.userId", name="members_user_id"),  # multikey
        ]


@register_document
class OrgInvite(Document):
    orgId: PydanticObjectId
    email: str
    token: str
    expiresAt: datetime
    acceptedAt: datetime | None = None

    class Settings:
        name = "orgInvites"
        indexes = [
            IndexModel("token", name="token_unique", unique=True),
            IndexModel("expiresAt", name="expires_at_ttl", expireAfterSeconds=0),
        ]


@register_document
class Repo(Document):
    orgId: PydanticObjectId
    githubRepoId: int
    owner: str
    name: str
    defaultBranch: str
    createdAt: datetime

    class Settings:
        name = "repos"
        indexes = [
            IndexModel(
                [("orgId", 1), ("githubRepoId", 1)],
                name="org_github_repo_unique",
                unique=True,
            ),
        ]


@register_document
class Feature(Document):
    """Org's feature-label registry. Distillation assigns each decision a
    feature from this set (or coins a new one). Features are the clustering
    key that makes the knowledge graph readable."""

    orgId: PydanticObjectId
    name: str  # slug-cased
    description: str
    createdAt: datetime

    class Settings:
        name = "features"
        indexes = [
            IndexModel(
                [("orgId", 1), ("name", 1)], name="org_name_unique", unique=True
            ),
        ]
