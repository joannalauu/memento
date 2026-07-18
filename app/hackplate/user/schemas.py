from datetime import datetime
from fastapi_users import schemas
from beanie import PydanticObjectId
from uuid import UUID


class UserRead(schemas.BaseUser[UUID]):
    sub: str | None = None


class UserCreate(schemas.BaseUserCreate):
    sub: str | None = None


class UserDocumentRead(schemas.BaseUser[PydanticObjectId]):
    sub: str | None = None
    name: str | None = None
    role: str | None = None
    githubUsername: str | None = None
    createdAt: datetime


class UserUpdate(schemas.BaseUserUpdate):
    sub: str | None = None
    name: str | None = None
    role: str | None = None
    githubUsername: str | None = None
