from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict

from app.orgs.models import OrgMember


class OrgCreate(BaseModel):
    name: str


class OrgUpdate(BaseModel):
    name: str | None = None
    githubInstallationId: int | None = None


class OrgRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    name: str
    slug: str
    githubInstallationId: int | None = None
    bbAssistantId: str
    members: list[OrgMember]
    createdAt: datetime


class UserPublic(BaseModel):
    """Non-sensitive user fields safe to surface to other org members."""

    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    email: str
    name: str | None = None
    role: str | None = None
    githubUsername: str | None = None
    createdAt: datetime


class OrgMemberRead(BaseModel):
    """An org member with the userId reference resolved to the full user."""

    user: UserPublic
    role: Literal["admin", "member"]
    joinedAt: datetime


class RepoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    orgId: PydanticObjectId
    githubRepoId: int
    owner: str
    name: str
    defaultBranch: str
    createdAt: datetime


class OrgInviteCreate(BaseModel):
    email: str


class OrgInviteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    orgId: PydanticObjectId
    email: str
    token: str
    expiresAt: datetime
    acceptedAt: datetime | None = None
