from datetime import datetime

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


class RepoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    orgId: PydanticObjectId
    githubRepoId: int
    owner: str
    name: str
    defaultBranch: str
    createdAt: datetime
