from fastapi_users import schemas
from beanie import PydanticObjectId
from uuid import UUID


class UserRead(schemas.BaseUser[UUID]):
    sub: str | None = None


class UserDocumentRead(schemas.BaseUser[PydanticObjectId]):
    sub: str | None = None


class UserCreate(schemas.BaseUserCreate):
    sub: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    sub: str | None = None
