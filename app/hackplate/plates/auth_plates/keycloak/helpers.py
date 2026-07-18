from __future__ import annotations
import logging
from fastapi import Depends
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from keycloak import KeycloakAdmin
from typing import Generic, TypeVar

from app.hackplate.user.managers import UserManager, UserDocumentManager
from app.hackplate.user.models import AbstractUser, AbstractUserDocument
from app.hackplate.user.dependencies import get_sqlmodel_user_db, get_beanie_user_db


logger = logging.getLogger(__name__)

auth_backend = AuthenticationBackend(
    name="keycloak",
    transport=BearerTransport(tokenUrl=""),
    get_strategy=lambda: JWTStrategy(secret="unused", lifetime_seconds=0),
)

UP = TypeVar("UP", AbstractUser, AbstractUserDocument)


class KeycloakSyncMixin(Generic[UP]):
    keycloak_admin: KeycloakAdmin

    async def on_after_update(self, user: UP, update_dict: dict, request=None):
        if not user.sub:
            return
        await self.keycloak_admin.a_update_user(
            user_id=user.sub,
            payload={
                k: v
                for k, v in {
                    "email": update_dict.get("email"),
                    "enabled": update_dict.get("is_active"),
                }.items()
                if v is not None
            },
        )
        logger.info(f"User {user.id} synced to Keycloak.")

    async def on_after_delete(self, user: UP, request=None):
        if not user.sub:
            return
        await self.keycloak_admin.a_delete_user(user_id=user.sub)
        logger.info(f"User {user.id} deleted from Keycloak.")


class KeycloakUserManager(KeycloakSyncMixin[AbstractUser], UserManager): ...


class KeycloakUserDocumentManager(
    KeycloakSyncMixin[AbstractUserDocument], UserDocumentManager
): ...


async def get_keycloak_sqlmodel_user_manager(user_db=Depends(get_sqlmodel_user_db)):
    yield KeycloakUserManager(user_db)


async def get_keycloak_beanie_user_manager(user_db=Depends(get_beanie_user_db)):
    yield KeycloakUserDocumentManager(user_db)
