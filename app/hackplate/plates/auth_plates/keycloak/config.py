from fastapi import status
from fastapi.exceptions import HTTPException
from keycloak import KeycloakAdmin, KeycloakOpenID, KeycloakOpenIDConnection

from app.hackplate.plates.abstract_plates import AuthPlate
from app.hackplate.plates.auth_plates.keycloak.env_settings import KeycloakSettings
from app.hackplate.plates.auth_plates.keycloak.helpers import (
    auth_backend,
    KeycloakSyncMixin,
    get_keycloak_beanie_user_manager,
    get_keycloak_sqlmodel_user_manager,
)
from app.hackplate.plates.auth_plates.keycloak.routes import keycloak_router_factory
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


class KeycloakPlate(AuthPlate):
    def __init__(self, toml_settings: AuthSettings, db_name: str):
        self.db_name = db_name
        self.env_settings = KeycloakSettings()

        KeycloakSyncMixin.keycloak_admin = KeycloakAdmin(
            connection=KeycloakOpenIDConnection(
                server_url=self.env_settings.host,
                realm_name=self.env_settings.realm,
                client_id=self.env_settings.client_id,
                client_secret_key=self.env_settings.client_secret,
                grant_type="client_credentials",
                verify=True,
            )
        )

        self.manager_dependency = (
            get_keycloak_beanie_user_manager
            if db_name == "mongo"
            else get_keycloak_sqlmodel_user_manager
        )
        self.read_schema = UserDocumentRead if db_name == "mongo" else UserRead
        self.keycloak_openid = KeycloakOpenID(
            server_url=self.env_settings.host,
            realm_name=self.env_settings.realm,
            client_id=self.env_settings.client_id,
            client_secret_key=self.env_settings.client_secret,
        )
        self.fastapi_users = make_fastapi_users(auth_backend, self.manager_dependency)

    async def register_auth_routes(self, app: Hackplate) -> None:
        app.include_router(
            keycloak_router_factory(self.env_settings, self.manager_dependency),
            tags=["auth"],
        )
        app.include_router(
            make_delete_me_router(self.fastapi_users),
            prefix="/users",
            tags=["users"],
        )
        app.include_router(
            self.fastapi_users.get_users_router(self.read_schema, UserUpdate),
            prefix="/users",
            tags=["users"],
        )

    async def authenticate(self, request: HackplateRequest) -> None:
        token = request.cookies.get("access_token")
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            await self.keycloak_openid.a_decode_token(token)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    async def get_current_user(
        self, request: HackplateRequest
    ) -> AbstractUser | AbstractUserDocument:
        token = request.cookies.get("access_token")
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            user_info = await self.keycloak_openid.a_decode_token(token)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        if self.db_name == "mongo":
            user_db = BeanieUserDatabaseAsync(get_user_model())
            user = await user_db.get_by_sub(user_info["sub"])
        else:
            async with request.app.state.config.db.get_db() as session:
                user_db = SQLModelUserDatabaseAsync(session, get_user_model())
                user = await user_db.get_by_sub(user_info["sub"])

        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user

    async def ping(self) -> bool:
        try:
            await self.keycloak_openid.a_well_known()
            return True
        except Exception:
            return False
