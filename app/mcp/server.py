"""
Minimal MCP server over JSON-RPC 2.0 (Streamable HTTP transport).

Implements the request/response half of MCP — initialize, tools/list,
tools/call, ping — returning application/json (we don't push server-initiated
SSE streams). Transport/auth live in routes.py; tools live in tools.py.
"""

import json
import logging
from typing import Any

from app.github.client import GitHubError
from app.mcp.tools import MCP_TOOLS, TOOLS_BY_NAME, McpContext, McpToolError

logger = logging.getLogger(__name__)

# Latest MCP protocol revision we implement. We echo the client's requested
# version on initialize when it sends one.
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "memento-mcp", "version": "0.1.0"}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def _call_tool(params: dict[str, Any], ctx: McpContext) -> dict[str, Any]:
    """Execute a tools/call. Tool failures become ``isError`` results (so the
    model can react) rather than JSON-RPC protocol errors."""
    name = params.get("name")
    args = params.get("arguments") or {}
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }
    try:
        output = await tool.handler(ctx, args)
    except (McpToolError, GitHubError) as exc:
        return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
    except Exception as exc:  # noqa: BLE001
        # Unexpected failures (e.g. Backboard transport errors, which the
        # context-engine deliberately propagates) become error results so the
        # model gets a message instead of the request 500-ing.
        logger.exception("MCP tool %r failed", name)
        return {
            "content": [
                {"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}
            ],
            "isError": True,
        }
    text = (
        output if isinstance(output, str) else json.dumps(output, indent=2, default=str)
    )
    return {"content": [{"type": "text", "text": text}], "isError": False}


async def dispatch(message: Any, ctx: McpContext) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns a response dict, or None for a
    notification (no ``id``) that needs only an HTTP-level ack."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error_response(None, -32600, "Invalid Request")

    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if not isinstance(method, str):
        return error_response(msg_id, -32600, "Invalid Request: missing method")

    # Notifications (e.g. notifications/initialized) carry no id — just ack.
    if msg_id is None:
        return None

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [t.definition() for t in MCP_TOOLS]})
    if method == "tools/call":
        return _result(msg_id, await _call_tool(params, ctx))

    return error_response(msg_id, -32601, f"Method not found: {method}")
