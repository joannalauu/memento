from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from pymongo.asynchronous.database import AsyncDatabase

from app.hackplate import HackplateRequest
from app.hackplate.dependencies import (
    hackplate_authenticate,
    hackplate_get_session,
    hackplate_get_client,
    hackplate_get_current_user,
)


async def get_session(request: HackplateRequest) -> AsyncGenerator[AsyncSession, None]:
    async for session in hackplate_get_session(request):
        yield session


async def get_client(request: HackplateRequest) -> AsyncDatabase:
    return await hackplate_get_client(request)


async def authenticate(request: HackplateRequest) -> None:
    await hackplate_authenticate(request)


async def get_current_user(user=Depends(hackplate_get_current_user)):
    return user
