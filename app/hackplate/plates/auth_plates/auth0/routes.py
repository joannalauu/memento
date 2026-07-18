import asyncio
import secrets

from collections.abc import Callable
from urllib.parse import urlencode

from auth0.authentication import GetToken, Users
from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse
from fastapi_users import BaseUserManager

from app.hackplate.user.schemas import UserCreate
from app.hackplate.plates.auth_plates.auth0.env_settings import Auth0Settings

from app.hackplate.hackplate_types import HackplateRequest


def auth0_router_factory(
    settings: Auth0Settings, manager_dependency: Callable
) -> APIRouter:
    get_token = GetToken(
        settings.domain, settings.client_id, client_secret=settings.client_secret
    )
    users_client = Users(settings.domain)
    auth0_router = APIRouter()

    @auth0_router.get("/auth/login")
    async def login(return_to: str | None = None):
        params = {
            "client_id": settings.client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": settings.callback_url,
            "audience": settings.audience,
        }
        # Carry an optional post-login destination through Auth0's `state` echo so
        # flows like accepting an org invite resume where they left off. Only a
        # same-origin path is allowed (re-checked in the callback) so `state`
        # can't be turned into an open redirect.
        if return_to and return_to.startswith("/") and not return_to.startswith("//"):
            params["state"] = return_to
        return RedirectResponse(
            f"https://{settings.domain}/authorize?{urlencode(params)}"
        )

    @auth0_router.get("/auth/callback")
    async def callback(
        code: str,
        state: str | None = None,
        user_manager: BaseUserManager = Depends(manager_dependency),
    ):
        try:
            tokens = await asyncio.to_thread(
                get_token.authorization_code, code, settings.callback_url
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token exchange failed. Try again later.",
            )

        try:
            user_info = await asyncio.to_thread(
                users_client.userinfo, tokens["access_token"]
            )
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

        # Resume a same-origin `return_to` carried through `state` (see /auth/login);
        # anything else falls back to the configured default. Re-validated here so
        # a hand-crafted `state` can't redirect off-origin.
        redirect_target = settings.redirect_uri
        if state and state.startswith("/") and not state.startswith("//"):
            redirect_target = state
        response = RedirectResponse(url=redirect_target)
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

    @auth0_router.get("/auth/logout")
    async def logout(request: HackplateRequest):
        params = urlencode(
            {
                "client_id": settings.client_id,
                "returnTo": settings.redirect_uri,
            }
        )
        response = RedirectResponse(f"https://{settings.domain}/v2/logout?{params}")
        response.delete_cookie("id_token")
        response.delete_cookie("access_token")
        return response

    return auth0_router
