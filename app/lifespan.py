from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from app.backboard.client import Backboard
from app.hackplate import Hackplate


@asynccontextmanager
async def pre_hackplate_lifespan(app: Hackplate) -> AsyncGenerator[None, None]:
    """
    Lifespan handler designated for user modification. runs before hackplate's lifespan handler.

    Args:
        app: initialized Hackplate object originating from main.py
    """
    yield


@asynccontextmanager
async def lifespan(app: Hackplate) -> AsyncGenerator[None, None]:
    """
    Lifespan handler designated for user modification. runs after hackplate's lifespan handler.

    Args:
        app: initialized Hackplate object originating from main.py
    """
    app.state.backboard = Backboard()
    try:
        yield
    finally:
        await app.state.backboard.aclose()
