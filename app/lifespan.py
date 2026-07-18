import asyncio
import contextlib
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from app.backboard.client import Backboard
from app.distillation.worker import worker_enabled, worker_loop
from app.github.client import GitHubApp
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
    app.state.github = GitHubApp()
    # T3.2: in-process consumer of the PipelineJobs the webhook enqueues.
    worker_task = (
        asyncio.create_task(worker_loop(app), name="pipeline-worker")
        if worker_enabled()
        else None
    )
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        await app.state.github.aclose()
        await app.state.backboard.aclose()
