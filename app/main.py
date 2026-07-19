from app.api_auth.routes import router as api_auth_router
from app.claude_hook.routes import router as claude_hook_router
from app.file_upload.routes import router as documents_router
from app.github.routes import router as github_router
from app.graph.ask import router as graph_ask_router
from app.graph.live import router as graph_live_router
from app.graph.routes import router as graph_router
from app.hackplate import Hackplate
from app.mcp.routes import router as mcp_router
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
    app.include_router(graph_router, prefix="/orgs", tags=["graph"])
    app.include_router(graph_live_router, prefix="/orgs", tags=["graph"])
    app.include_router(graph_ask_router, prefix="/orgs", tags=["graph"])
    app.include_router(claude_hook_router, prefix="/ingest", tags=["ingest"])
    app.include_router(documents_router, prefix="/documents", tags=["documents"])
    app.include_router(github_router, prefix="/github", tags=["github"])
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])


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
