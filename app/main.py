from app.api_auth.routes import router as api_auth_router
from app.claude_hook.routes import router as claude_hook_router
from app.hackplate import Hackplate
from app.hackplate.lifespan import configure
from app.lifespan import lifespan, pre_hackplate_lifespan
from app.orgs.routes import router as orgs_router


def register_routes(app: Hackplate) -> None:
    """
    Function for registering routers.

    Args:
        app: initialized Hackplate object originating from main.py
    """
    app.include_router(api_auth_router, prefix="/api-keys", tags=["api-keys"])
    app.include_router(orgs_router, prefix="/orgs", tags=["orgs"])
    app.include_router(claude_hook_router, prefix="/ingest", tags=["ingest"])


app = Hackplate(
    pre_hackplate_lifespan=pre_hackplate_lifespan,
    post_hackplate_lifespan=lifespan,
)

configure(
    app,
    [
        register_routes,
    ],
)
