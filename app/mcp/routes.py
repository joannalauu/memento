"""
MCP endpoint (Streamable HTTP transport).

A single POST /mcp carries JSON-RPC messages. Every request is authenticated by
API key (Authorization: Bearer <key>) via the shared api_auth dependency, which
resolves the caller to a user + org — so all MCP tools are org-scoped without the
client ever passing an org id.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api_auth.dependencies import ApiKeyPrincipal, get_api_key_principal
from app.mcp.server import PROTOCOL_VERSION, dispatch, error_response
from app.mcp.tools import McpContext
from app.orgs.crud import get_org

router = APIRouter()


@router.post("")
async def mcp_endpoint(
    request: Request,
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
):
    """Handle a JSON-RPC message (or list of them) against the API key's org."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            error_response(None, -32700, "Parse error"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # request → API key → user + org. The API key carries the org id, so the
    # whole tool surface is scoped to it.
    org = await get_org(principal.api_key.orgId)
    if org is None:
        return JSONResponse(
            error_response(None, -32603, "The API key's organization no longer exists"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ctx = McpContext(
        principal=principal,
        org=org,
        github=request.app.state.github,
        backboard=request.app.state.backboard,
        # Optional client-supplied session key; a graph-tool handler forwards it
        # so traversal events route to that session's graph view (T4.5/T5.2).
        session_id=request.headers.get("X-Session-Id"),
    )

    # A JSON-RPC batch (list) is handled per-message; notifications drop out.
    if isinstance(body, list):
        responses = [r for r in [await dispatch(m, ctx) for m in body] if r is not None]
        if not responses:
            return Response(status_code=status.HTTP_202_ACCEPTED)
        return JSONResponse(responses)

    response = await dispatch(body, ctx)
    if response is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(response)


@router.get("")
async def mcp_get() -> Response:
    """We don't support server-initiated SSE streams, so reject the GET stream
    open with 405 (allowed by the Streamable HTTP spec)."""
    return Response(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        headers={"Allow": "POST", "MCP-Protocol-Version": PROTOCOL_VERSION},
    )
