from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header
from sqlmodel.ext.asyncio.session import AsyncSession
from pymongo.asynchronous.database import AsyncDatabase

from app.api_auth.dependencies import (  # noqa: F401  re-exported for convenience
    ApiKeyPrincipal,
    get_api_key_principal,
    get_api_key_user,
)
from app.hackplate import HackplateRequest
from app.hackplate.dependencies import (
    hackplate_authenticate,
    hackplate_get_session,
    hackplate_get_client,
    hackplate_get_current_user,
)
from app.traversal import TraversalTag


async def get_session(request: HackplateRequest) -> AsyncGenerator[AsyncSession, None]:
    async for session in hackplate_get_session(request):
        yield session


async def get_client(request: HackplateRequest) -> AsyncDatabase:
    return await hackplate_get_client(request)


async def authenticate(request: HackplateRequest) -> None:
    await hackplate_authenticate(request)


async def get_current_user(user=Depends(hackplate_get_current_user)):
    return user


async def get_traversal_tag(
    x_session_id: Annotated[str | None, Header()] = None,
) -> TraversalTag | None:
    """Traversal-event stamp for a web request: the caller's session key from the
    `X-Session-Id` header, tagged `source="web"`. Returns None when the header is
    absent (a web Q&A route then calls the graph tools untagged — no emission).
    T4.5 routes depend on this and forward the tag into find_entry_points/walk_graph."""
    return TraversalTag(x_session_id, "web") if x_session_id else None
