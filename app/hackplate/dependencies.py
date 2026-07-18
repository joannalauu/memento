from collections.abc import AsyncGenerator

from sqlmodel.ext.asyncio.session import AsyncSession
from pymongo.asynchronous.database import AsyncDatabase

from app.hackplate.hackplate_types import HackplateRequest


async def hackplate_get_session(
    request: HackplateRequest,
) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.config.db.get_db() as session:
        yield session


async def hackplate_get_client(request: HackplateRequest) -> AsyncDatabase:
    """Returns the raw pymongo async database. Prefer using Beanie Document models directly."""
    return await request.app.state.config.db.get_db()


async def hackplate_authenticate(request: HackplateRequest) -> None:
    await request.app.state.config.auth.authenticate(request)


async def hackplate_get_current_user(request: HackplateRequest):
    return await request.app.state.config.auth.get_current_user(request)
