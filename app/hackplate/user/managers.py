import logging
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users.exceptions import InvalidID
from beanie import PydanticObjectId
from uuid import UUID
from typing import Any
import bson.errors

from app.hackplate.user.models import AbstractUser, AbstractUserDocument
from app.hackplate.plates.auth_plates.local.env_settings import LocalAuthSettings

logger = logging.getLogger(__name__)


class ObjectIDIDMixin:
    def parse_id(self, value: Any) -> PydanticObjectId:
        try:
            return PydanticObjectId(value)
        except (bson.errors.InvalidId, TypeError) as e:
            raise InvalidID() from e


class UserManager(UUIDIDMixin, BaseUserManager[AbstractUser, UUID]):
    secret_key = LocalAuthSettings().secret_key
    reset_password_token_secret = secret_key + "_reset"
    verification_token_secret = secret_key + "_verify"

    async def on_after_register(self, user: AbstractUser, request=None):
        logger.info(f"User {user.id} registered.")

    async def on_after_forgot_password(
        self, user: AbstractUser, token: str, request=None
    ):
        logger.info(f"User {user.id} forgot password. Token: {token}")

    async def on_after_request_verify(
        self, user: AbstractUser, token: str, request=None
    ):
        logger.info(f"User {user.id} requested verification. Token: {token}")


class UserDocumentManager(
    ObjectIDIDMixin, BaseUserManager[AbstractUserDocument, PydanticObjectId]
):
    secret_key = LocalAuthSettings().secret_key
    reset_password_token_secret = secret_key + "_reset"
    verification_token_secret = secret_key + "_verify"

    async def on_after_register(self, user: AbstractUserDocument, request=None):
        logger.info(f"User {user.id} registered.")

    async def on_after_forgot_password(
        self, user: AbstractUserDocument, token: str, request=None
    ):
        logger.info(f"User {user.id} forgot password. Token: {token}")

    async def on_after_request_verify(
        self, user: AbstractUserDocument, token: str, request=None
    ):
        logger.info(f"User {user.id} requested verification. Token: {token}")
