from __future__ import annotations

import logging
from typing import Generic, TypeVar

from fastapi import Depends
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from auth0.management import AsyncManagementClient

from app.hackplate.user.dependencies import get_beanie_user_db, get_sqlmodel_user_db
from app.hackplate.user.managers import UserDocumentManager, UserManager
from app.hackplate.user.models import AbstractUser, AbstractUserDocument

logger = logging.getLogger(__name__)

auth_backend = AuthenticationBackend(
    name="auth0",
    transport=BearerTransport(tokenUrl=""),
    get_strategy=lambda: JWTStrategy(secret="unused", lifetime_seconds=0),
)

UP = TypeVar("UP", AbstractUser, AbstractUserDocument)


class Auth0SyncMixin(Generic[UP]):
    mgmt_client: AsyncManagementClient

    async def on_after_update(self, user: UP, update_dict: dict, request=None):
        if not user.sub:
            return
        kwargs = {}
        if "email" in update_dict:
            kwargs["email"] = update_dict["email"]
        if "is_active" in update_dict:
            kwargs["blocked"] = not update_dict["is_active"]
        if not kwargs:
            return
        try:
            await self.mgmt_client.users.update(user.sub, **kwargs)
            logger.info(f"User {user.id} synced to Auth0.")
        except Exception as e:
            logger.error(f"Failed to sync user {user.id} to Auth0: {e}")

    async def on_after_delete(self, user: UP, request=None):
        if not user.sub:
            return
        try:
            await self.mgmt_client.users.delete(user.sub)
            logger.info(f"User {user.id} deleted from Auth0.")
        except Exception as e:
            logger.error(f"Failed to delete user {user.id} from Auth0: {e}")


class Auth0UserManager(Auth0SyncMixin[AbstractUser], UserManager): ...


class Auth0UserDocumentManager(
    Auth0SyncMixin[AbstractUserDocument], UserDocumentManager
): ...


async def get_auth0_sqlmodel_user_manager(user_db=Depends(get_sqlmodel_user_db)):
    yield Auth0UserManager(user_db)


async def get_auth0_beanie_user_manager(user_db=Depends(get_beanie_user_db)):
    yield Auth0UserDocumentManager(user_db)
