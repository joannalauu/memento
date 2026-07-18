from fastapi_users.models import ID
from typing import Any, Dict, Generic, Optional, Type, TypeVar
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select
from fastapi_users.db.base import BaseUserDatabase
from beanie import PydanticObjectId

from app.hackplate.user.models import AbstractUserDocument, AbstractUser

UP_SQLMODEL = TypeVar("UP_SQLMODEL", bound=AbstractUser)


class SQLModelUserDatabaseAsync(
    Generic[UP_SQLMODEL, ID], BaseUserDatabase[UP_SQLMODEL, ID]
):
    """
    Database adapter for SQLModel working purely asynchronously. Borrowed from fastapi-users-db-sqlmodel
    """

    session: AsyncSession
    user_model: Type[UP_SQLMODEL]

    def __init__(
        self,
        session: AsyncSession,
        user_model: Type[UP_SQLMODEL],
    ):
        self.session = session
        self.user_model = user_model

    async def get(self, id: UP_SQLMODEL) -> Optional[UP_SQLMODEL]:
        """Get a single user by id."""
        return await self.session.get(self.user_model, id)

    async def get_by_email(self, email: str) -> Optional[UP_SQLMODEL]:
        """Get a single user by email."""
        statement = select(self.user_model).where(  # type: ignore
            func.lower(self.user_model.email) == func.lower(email)
        )
        results = await self.session.execute(statement)
        object = results.first()
        if object is None:
            return None
        return object[0]

    async def get_by_sub(self, sub: str) -> Optional[UP_SQLMODEL]:
        """Get a single user by sub."""
        statement = select(self.user_model).where(self.user_model.sub == sub)
        results = await self.session.execute(statement)
        object = results.first()
        if object is None:
            return None
        return object[0]

    async def create(self, create_dict: Dict[str, Any]) -> UP_SQLMODEL:
        """Create a user."""
        user = self.user_model(**create_dict)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update(
        self, user: UP_SQLMODEL, update_dict: Dict[str, Any]
    ) -> UP_SQLMODEL:
        for key, value in update_dict.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete(self, user: UP_SQLMODEL) -> None:
        await self.session.delete(user)
        await self.session.commit()


UP_BEANIE = TypeVar("UP_BEANIE", bound=AbstractUserDocument)


class BeanieUserDatabaseAsync(
    Generic[UP_BEANIE], BaseUserDatabase[UP_BEANIE, PydanticObjectId]
):
    """
    Database adapter for Beanie.

    :param user_model: Beanie user model.
    """

    def __init__(
        self,
        user_model: type[UP_BEANIE],
    ):
        self.user_model = user_model

    async def get(self, id: PydanticObjectId) -> UP_BEANIE | None:
        """Get a single user by id."""
        return await self.user_model.get(id)  # type: ignore

    async def get_by_email(self, email: str) -> UP_BEANIE | None:
        """Get a single user by email."""
        return await self.user_model.find_one(
            self.user_model.email == email,
            collation=self.user_model.Settings.email_collation,
        )

    async def get_by_sub(self, sub: str) -> UP_BEANIE | None:
        """Get a single user by sub."""
        return await self.user_model.find_one(self.user_model.sub == sub)

    async def create(self, create_dict: dict[str, Any]) -> UP_BEANIE:
        """Create a user."""
        user = self.user_model(**create_dict)
        await user.create()
        return user

    async def update(self, user: UP_BEANIE, update_dict: dict[str, Any]) -> UP_BEANIE:
        """Update a user."""
        for key, value in update_dict.items():
            setattr(user, key, value)
        await user.save()
        return user

    async def delete(self, user: UP_BEANIE) -> None:
        """Delete a user."""
        await user.delete()
