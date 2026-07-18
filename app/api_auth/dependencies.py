from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api_auth import crud
from app.api_auth.models import ApiKey
from app.orgs.models import User

# `auto_error=False` so a missing/malformed header yields our own 401 (with a
# WWW-Authenticate hint) instead of FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False, description="API key as `Bearer <key>`")

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or missing API key",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass
class ApiKeyPrincipal:
    """The authenticated caller resolved from an API key: the owning user plus
    the key record (which carries `orgId` for org-scoped authorization)."""

    user: User
    api_key: ApiKey


async def get_api_key_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ApiKeyPrincipal:
    """Authenticate a request by the API key in the `Authorization: Bearer <key>`
    header. Resolves the key to its owning user and org; raises 401 if the header
    is missing, the key doesn't match, or the owning user no longer exists / is
    inactive. Use as a route dependency to require a valid key."""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED

    api_key = await crud.resolve_api_key(credentials.credentials)
    if api_key is None:
        raise _UNAUTHENTICATED

    user = await User.get(api_key.userId)
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED

    return ApiKeyPrincipal(user=user, api_key=api_key)


async def get_api_key_user(
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> User:
    """Drop-in alternative to `get_current_user` for API-key-authenticated
    routes: yields just the authenticated `User`."""
    return principal.user
