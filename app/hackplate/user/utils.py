import importlib
from functools import lru_cache
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi_users import BaseUserManager, FastAPIUsers

from app.hackplate.toml_settings import GeneralSettings
from app.hackplate.user.models import AbstractUser, AbstractUserDocument


@lru_cache(maxsize=1)
def get_user_model() -> type[AbstractUser] | type[AbstractUserDocument]:
    settings = GeneralSettings()
    module_path, class_name = settings.auth_user_model.rsplit(".", 1)
    module = importlib.import_module(module_path)
    model = getattr(module, class_name)
    if not issubclass(model, (AbstractUser, AbstractUserDocument)):
        raise ValueError(
            f"{settings.auth_user_model} must inherit from AbstractUser or AbstractUserDocument"
        )
    return model


def make_fastapi_users(auth_backend, manager_dependency):
    return FastAPIUsers[AbstractUser, UUID](
        manager_dependency,
        [auth_backend],
    )


def make_delete_me_router(
    fastapi_users: FastAPIUsers,
    get_current_active_user,
    cookie_names: list[str] | None = None,
    secure_cookies: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.delete(
        "/me",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        name="users:delete_current_user",
    )
    async def delete_me(
        request: Request,
        user=Depends(get_current_active_user),
        user_manager: BaseUserManager = Depends(fastapi_users.get_user_manager),
    ):
        await user_manager.delete(user, request=request)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        for name in cookie_names or []:
            response.delete_cookie(
                name, httponly=True, secure=secure_cookies, samesite="lax"
            )
        return response

    return router
