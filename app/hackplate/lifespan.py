import logging
import asyncio
from contextlib import asynccontextmanager, AsyncExitStack
from collections.abc import AsyncGenerator, Callable, Iterable

from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse

from app.hackplate.config import BackendConfig
from app.hackplate.cors import register_cors_middleware
from app.hackplate.exceptions import register_exception_handlers
from app.hackplate.logging import setup_logging
from app.hackplate.hackplate_types import Hackplate, HackplateRequest
from app.hackplate.toml_settings import BackendTOMLSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def base_lifespan(app: Hackplate) -> AsyncGenerator[None, None]:
    settings = BackendTOMLSettings()
    app.state.settings = settings
    config = BackendConfig(settings)
    app.state.config = config
    yield


@asynccontextmanager
async def config_lifespan(app: Hackplate) -> AsyncGenerator[None, None]:
    setup_logging()
    await app.state.config.db.connect()
    logger.info("Successful database connection!")
    if not await app.state.config.db.ping():
        logger.exception("Database ping failed.")
        await app.state.config.db.disconnect()
        raise RuntimeError("Database ping failed.")
    logger.info("Database: PONG")
    if not await app.state.config.auth.ping():
        logger.exception("Auth ping failed.")
        await app.state.config.db.disconnect()
        raise RuntimeError("Auth ping failed.")
    logger.info("Auth: PONG")
    await app.state.config.auth.register_auth_routes(app)
    yield
    await app.state.config.db.disconnect()


@asynccontextmanager
async def hackplate_lifespan(app: Hackplate) -> AsyncGenerator[None, None]:
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(base_lifespan(app))
        if app.pre_hackplate_lifespan:
            await stack.enter_async_context(app.pre_hackplate_lifespan(app))
        await stack.enter_async_context(config_lifespan(app))
        if app.post_hackplate_lifespan:
            await stack.enter_async_context(app.post_hackplate_lifespan(app))
        yield


def register_root_redirect(app: Hackplate) -> None:
    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/docs")


def register_health_ping(app: Hackplate) -> None:
    @app.get("/ping")
    async def ping(request: HackplateRequest) -> dict[str, str]:
        db_response, auth_response = await asyncio.gather(
            request.app.state.config.db.ping(),
            request.app.state.config.auth.ping(),
        )
        if not db_response and not auth_response:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database and Auth Ping Failed.",
            )
        if not db_response:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database Ping Failed.",
            )
        if not auth_response:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth Ping Failed.",
            )
        return {"message": "PONG"}


def configure(
    app: Hackplate, register_functions: Iterable[Callable[[Hackplate], None]]
):
    """
    Centralizes app configuration logic

    Args:
        app: initialized Hackplate object originating from main.py
        register_functions: list of functions with a single `app: Hackplate` param
    """
    register_exception_handlers(app)
    register_cors_middleware(app)
    register_root_redirect(app)
    register_health_ping(app)

    for fn in register_functions:
        try:
            fn(app)
        except Exception as e:
            raise RuntimeError(f"Failed to register {fn.__name__}") from e
