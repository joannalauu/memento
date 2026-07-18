from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi_users.jwt import decode_jwt
from jwt import PyJWTError

from app.hackplate.plates.abstract_plates import AuthPlate
from app.hackplate.toml_settings import AuthSettings
from app.hackplate.user.models import AbstractUser, AbstractUserDocument
from app.hackplate.user.utils import (
    make_fastapi_users,
    get_user_model,
    make_delete_me_router,
)
from app.hackplate.user.dependencies import (
    get_sqlmodel_user_manager,
    get_beanie_user_manager,
)
from app.hackplate.plates.auth_plates.local.helpers import (
    auth_backend,
    get_jwt_strategy,
)
from app.hackplate.user.schemas import (
    UserCreate,
    UserDocumentRead,
    UserRead,
    UserUpdate,
)
from app.hackplate.user.adapters import (
    SQLModelUserDatabaseAsync,
    BeanieUserDatabaseAsync,
)
from app.hackplate.user.managers import UserManager, UserDocumentManager
from app.hackplate.hackplate_types import Hackplate, HackplateRequest

_JWT_AUDIENCE = ["fastapi-users:auth"]
_JWT_ALGORITHM = "HS256"


class LocalPlate(AuthPlate):
    def __init__(self, toml_settings: AuthSettings, db_name: str):
        self.db_name = db_name
        self._secret = get_jwt_strategy().secret
        manager_dep = get_sqlmodel_user_manager
        if db_name == "mongo":
            manager_dep = get_beanie_user_manager

        self.read_schema = UserDocumentRead if db_name == "mongo" else UserRead
        self.fastapi_users = make_fastapi_users(auth_backend, manager_dep)

    async def register_auth_routes(self, app: Hackplate) -> None:
        app.include_router(
            self.fastapi_users.get_auth_router(auth_backend),
            prefix="/auth/jwt",
            tags=["auth"],
        )
        app.include_router(
            self.fastapi_users.get_register_router(self.read_schema, UserCreate),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            self.fastapi_users.get_reset_password_router(),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            self.fastapi_users.get_verify_router(self.read_schema),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            make_delete_me_router(
                self.fastapi_users,
                self.fastapi_users.current_user(active=True),
            ),
            prefix="/users",
            tags=["users"],
        )
        app.include_router(
            self.fastapi_users.get_users_router(self.read_schema, UserUpdate),
            prefix="/users",
            tags=["users"],
        )

    async def authenticate(self, request: HackplateRequest) -> None:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        token = auth_header[7:]
        try:
            decode_jwt(token, self._secret, _JWT_AUDIENCE, [_JWT_ALGORITHM])
        except PyJWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    async def get_current_user(
        self, request: HackplateRequest
    ) -> AbstractUser | AbstractUserDocument:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        token = auth_header[7:]
        strategy = get_jwt_strategy()

        if self.db_name == "mongo":
            user_db = BeanieUserDatabaseAsync(get_user_model())
            user_manager = UserDocumentManager(user_db)
            user: AbstractUserDocument = await strategy.read_token(token, user_manager)
        else:
            async with request.app.state.config.db.get_db() as session:
                user_db = SQLModelUserDatabaseAsync(session, get_user_model())
                user_manager = UserManager(user_db)
                user: AbstractUser = await strategy.read_token(token, user_manager)

        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user

    async def ping(self) -> bool:
        return bool(self._secret)
