import logging
import asyncio
import httpx
import jwt

from jwt import PyJWKClient
from auth0.management import AsyncManagementClient
from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from fastapi_users import BaseUserManager

from app.hackplate.plates.abstract_plates import AuthPlate
from app.hackplate.plates.auth_plates.auth0.env_settings import Auth0Settings
from app.hackplate.plates.auth_plates.auth0.helpers import (
    auth_backend,
    Auth0SyncMixin,
    get_auth0_beanie_user_manager,
    get_auth0_sqlmodel_user_manager,
)
from app.hackplate.plates.auth_plates.auth0.routes import auth0_router_factory
from app.hackplate.toml_settings import AuthSettings
from app.hackplate.user.adapters import (
    BeanieUserDatabaseAsync,
    SQLModelUserDatabaseAsync,
)
from app.hackplate.user.models import AbstractUser, AbstractUserDocument
from app.hackplate.user.schemas import UserDocumentRead, UserRead, UserUpdate
from app.hackplate.user.utils import (
    get_user_model,
    make_delete_me_router,
    make_fastapi_users,
)

from app.hackplate.hackplate_types import Hackplate, HackplateRequest

logger = logging.getLogger(__name__)
_JWKS_TIMEOUT_SECONDS = 5.0


class Auth0Plate(AuthPlate):
    def __init__(self, toml_settings: AuthSettings, db_name: str):
        self.db_name = db_name
        self.env_settings = Auth0Settings()

        Auth0SyncMixin.mgmt_client = AsyncManagementClient(
            domain=self.env_settings.domain,
            client_id=self.env_settings.m2m_client_id,
            client_secret=self.env_settings.m2m_client_secret,
        )

        self.manager_dependency = (
            get_auth0_beanie_user_manager
            if db_name == "mongo"
            else get_auth0_sqlmodel_user_manager
        )
        self.read_schema = UserDocumentRead if db_name == "mongo" else UserRead
        self.fastapi_users = make_fastapi_users(auth_backend, self.manager_dependency)
        self._jwks_client = PyJWKClient(
            f"https://{self.env_settings.domain}/.well-known/jwks.json"
        )
        self._issuer = f"https://{self.env_settings.domain}/"
        self._audience = self.env_settings.audience

    async def _verify_access_token(self, token: str) -> dict:
        signing_key = await asyncio.to_thread(
            self._jwks_client.get_signing_key_from_jwt, token
        )
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer,
        )

    async def register_auth_routes(self, app: Hackplate) -> None:
        app.include_router(
            auth0_router_factory(self.env_settings, self.manager_dependency),
            tags=["auth"],
        )
        app.include_router(
            make_delete_me_router(
                self.fastapi_users,
                self.get_current_user,
                cookie_names=["id_token", "access_token"],
                secure_cookies=self.env_settings.secure_cookies,
            ),
            prefix="/users",
            tags=["users"],
        )

        users_router = APIRouter()

        @users_router.get("/me", response_model=self.read_schema)
        async def get_me(user=Depends(self.get_current_user)):
            return user

        @users_router.patch("/me", response_model=self.read_schema)
        async def patch_me(
            update: UserUpdate,
            user=Depends(self.get_current_user),
            user_manager: BaseUserManager = Depends(self.manager_dependency),
        ):
            return await user_manager.update(update, user)

        app.include_router(users_router, prefix="/users", tags=["users"])

    async def authenticate(self, request: HackplateRequest) -> None:
        access_token = request.cookies.get("access_token")
        if not access_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            await self._verify_access_token(access_token)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    async def get_current_user(self, request: HackplateRequest):
        access_token = request.cookies.get("access_token")
        if not access_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            payload = await self._verify_access_token(access_token)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        if self.db_name == "mongo":
            user_db = BeanieUserDatabaseAsync(get_user_model())
            user: AbstractUserDocument = await user_db.get_by_sub(payload["sub"])
        else:
            async with request.app.state.config.db.get_db() as session:
                user_db = SQLModelUserDatabaseAsync(session, get_user_model())
                user: AbstractUser = await user_db.get_by_sub(payload["sub"])

        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user

    async def ping(self) -> bool:
        jwks_url = f"https://{self.env_settings.domain}/.well-known/jwks.json"
        try:
            async with httpx.AsyncClient(timeout=_JWKS_TIMEOUT_SECONDS) as client:
                response = await client.get(jwks_url)
            response.raise_for_status()
            return True
        except Exception:
            return False
