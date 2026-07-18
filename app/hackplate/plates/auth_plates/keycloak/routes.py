import secrets

from collections.abc import Callable
from fastapi import APIRouter, status, Depends
from fastapi.responses import RedirectResponse
from fastapi.exceptions import HTTPException
from urllib.parse import urlencode
from fastapi_users import BaseUserManager
from keycloak import KeycloakOpenID

from app.hackplate.user.schemas import UserCreate
from app.hackplate.plates.auth_plates.keycloak.env_settings import KeycloakSettings

from app.hackplate.hackplate_types import HackplateRequest


def keycloak_router_factory(
    settings: KeycloakSettings, manager_dependency: Callable
) -> APIRouter:
    keycloak_openid = KeycloakOpenID(
        server_url=settings.host,
        realm_name=settings.realm,
        client_id=settings.client_id,
        client_secret_key=settings.client_secret,
    )
    keycloak_router = APIRouter()

    @keycloak_router.get("/auth/login")
    async def login():
        params = urlencode(
            {
                "client_id": settings.client_id,
                "response_type": "code",
                "scope": "openid profile email",
                "redirect_uri": settings.callback_url,
            }
        )
        url = f"{settings.external_url}/realms/{settings.realm}/protocol/openid-connect/auth?{params}"
        return RedirectResponse(url)

    @keycloak_router.get("/auth/callback")
    async def callback(
        code: str, user_manager: BaseUserManager = Depends(manager_dependency)
    ):
        try:
            tokens = await keycloak_openid.a_token(
                grant_type="authorization_code",
                code=code,
                redirect_uri=settings.callback_url,
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token exchange failed. Try again later.",
            )

        try:
            user_info = await keycloak_openid.a_decode_token(tokens["id_token"])
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token."
            )

        email = user_info["email"]
        sub = user_info["sub"]

        user = await user_manager.user_db.get_by_sub(sub)

        if not user:
            user = await user_manager.user_db.get_by_email(email)
            if user:
                await user_manager.user_db.update(user, {"sub": sub})
            else:
                try:
                    await user_manager.create(
                        UserCreate(
                            email=email,
                            password=secrets.token_urlsafe(32),
                            is_verified=True,
                            is_active=True,
                            is_superuser=False,
                            sub=sub,
                        )
                    )
                except Exception:
                    user = await user_manager.user_db.get_by_email(email)
                    if not user:
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
                        )

        response = RedirectResponse(url=settings.redirect_uri)
        response.set_cookie(
            "id_token",
            tokens["id_token"],
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
        )
        response.set_cookie(
            "access_token",
            tokens["access_token"],
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
        )
        return response

    @keycloak_router.get("/auth/logout")
    async def logout(request: HackplateRequest):
        id_token = request.cookies.get("id_token")
        if not id_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in."
            )

        params = urlencode(
            {
                "client_id": settings.client_id,
                "post_logout_redirect_uri": settings.redirect_uri,
                "id_token_hint": id_token,
            }
        )
        url = f"{settings.external_url}/realms/{settings.realm}/protocol/openid-connect/logout?{params}"
        response = RedirectResponse(url)
        response.delete_cookie("id_token")
        response.delete_cookie("access_token")
        return response

    return keycloak_router
