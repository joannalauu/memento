"""
Generic tool-executing loop over the Backboard wrapper. Transport-agnostic and
toolset-agnostic: takes any {name → async fn} registry (see
app/github/tools.py for the GitHub one).

Backboard does not execute tools — when the model requests one it pauses with
status REQUIRES_ACTION (non-streaming) or a tool_submit_required SSE event
(streaming). These loops run the requested functions and submit ALL outputs of
a round together in one POST /threads/tool-outputs (partial submission stalls
the run server-side), repeating until the model produces a final answer.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

from backboard.models import ChatMessagesResponse, ToolCall, ToolDefinition, ToolOutput
from pydantic import ValidationError

from app.backboard.client import Backboard, Uuid

ToolFn = Callable[[dict[str, Any]], Awaitable[str]]
ToolRegistry = Mapping[str, ToolFn]

REQUIRES_ACTION = "REQUIRES_ACTION"
DEFAULT_MAX_ROUNDS = 10
MAX_TOOL_OUTPUT_CHARS = 100_000


class ExecutorError(RuntimeError):
    pass


class MaxRoundsExceeded(ExecutorError):
    def __init__(self, thread_id: str | None, rounds: int) -> None:
        super().__init__(
            f"Tool loop exceeded {rounds - 1} rounds on thread {thread_id}"
        )
        self.thread_id = thread_id
        self.rounds = rounds


def _normalize_tool_call(
    tc: ToolCall | dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None]:
    """(call_id, name, args). Accepts an SDK ToolCall (non-streaming path) or a
    raw dict (tool_submit_required SSE event). args is None when the arguments
    JSON is unparseable — surfaced to the model as an error output."""
    if isinstance(tc, ToolCall):
        raw = tc.function.arguments
        call_id, name = tc.id, tc.function.name
    else:
        function = tc.get("function") or {}
        raw = function.get("arguments")
        call_id, name = tc.get("id", ""), function.get("name", "")
    if not raw or not str(raw).strip():
        return call_id, name, {}
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return call_id, name, None
    return call_id, name, args if isinstance(args, dict) else None


async def _run_one(
    registry: ToolRegistry,
    call_id: str,
    name: str,
    args: dict[str, Any] | None,
) -> dict[str, str]:
    if args is None:
        return {
            "tool_call_id": call_id,
            "output": f"Error: could not parse arguments for tool '{name}'",
        }
    fn = registry.get(name)
    if fn is None:
        return {
            "tool_call_id": call_id,
            "output": f"Error: unknown tool '{name}'. Available: {sorted(registry)}",
        }
    try:
        out = await fn(args)
    except Exception as exc:  # noqa: BLE001 — a failing tool must not kill the run
        out = f"Error executing tool '{name}': {type(exc).__name__}: {exc}"
    return {"tool_call_id": call_id, "output": out[:MAX_TOOL_OUTPUT_CHARS]}


async def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: Sequence[ToolCall | dict[str, Any]],
) -> list[ToolOutput | dict[str, str]]:
    """Run ALL calls concurrently; one output per call, order-preserving —
    ready to submit together in a single submit_tool_outputs call."""
    normalized = [_normalize_tool_call(tc) for tc in tool_calls]
    results = await asyncio.gather(*(_run_one(registry, *n) for n in normalized))
    # Runtime values are plain dicts (a valid member of the SDK's declared
    # List[ToolOutput | dict[str, str]] param) — cast so callers passing this
    # straight into submit_tool_outputs/stream_tool_outputs satisfy invariant
    # List typing without an unnecessary copy/rebuild.
    return cast("list[ToolOutput | dict[str, str]]", list(results))


async def _try_cancel(bb: Backboard, thread_id: Any, run_id: str | None) -> None:
    if not thread_id or not run_id:
        return
    try:
        await bb.cancel_run(thread_id, run_id)
    except Exception:  # noqa: BLE001 — best-effort cleanup only
        pass


async def run_with_tools(
    bb: Backboard,
    content: str,
    *,
    tools: Sequence[ToolDefinition | dict[str, Any]],
    registry: ToolRegistry,
    thread_id: Uuid | None = None,
    assistant_id: Uuid | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    **send_kwargs: Any,
) -> ChatMessagesResponse:
    """Non-streaming loop: send → execute tool calls while REQUIRES_ACTION →
    final response. Read `.content` for the answer, `.thread_id` to continue."""
    response = await bb.send_message(
        content,
        thread_id=thread_id,
        assistant_id=assistant_id,
        tools=list(tools),
        **send_kwargs,
    )
    rounds = 0
    while response.status == REQUIRES_ACTION:
        calls = response.tool_calls or []
        if not calls:
            raise ExecutorError("REQUIRES_ACTION response carried no tool calls")
        rounds += 1
        if rounds > max_rounds:
            await _try_cancel(bb, response.thread_id, response.run_id)
            raise MaxRoundsExceeded(str(response.thread_id), rounds)
        outputs = await execute_tool_calls(registry, calls)
        # thread_id from the response — covers auto-created threads.
        assert response.thread_id is not None, (
            "REQUIRES_ACTION response has no thread_id"
        )
        response = await bb.submit_tool_outputs(response.thread_id, outputs)
    return response


def _extract_stream_tool_calls(
    event: dict[str, Any],
) -> list[ToolCall | dict[str, Any]]:
    """Look for tool_calls at the known event locations and validate each into
    a ToolCall, falling back to the raw dict on mismatch.

    Expected payload: the SDK's `_stream_request` (backboard/client.py) does a
    bare `json.loads()` per SSE line and yields it completely unvalidated for
    every event type except error/run_failed/run_ended — unlike the REST path,
    where `ChatMessagesResponse.tool_calls` explicitly parses each entry via
    `ToolCall.model_validate`. `tool_submit_required` appears nowhere in the
    SDK's models or tests, so nothing guarantees its tool_calls entries match
    the `{id, type, function: {name, arguments}}` ToolCall shape — even though
    it's presumably the same backend domain object as the REST path. Validate
    optimistically; a raw dict survives downstream via `_normalize_tool_call`.
    """
    for candidate in (event, event.get("data"), event.get("message")):
        if isinstance(candidate, dict) and candidate.get("tool_calls"):
            normalized: list[ToolCall | dict[str, Any]] = []
            for tc in candidate["tool_calls"]:
                try:
                    normalized.append(ToolCall.model_validate(tc))
                except ValidationError:
                    normalized.append(tc)
            return normalized
    return []


async def stream_with_tools(
    bb: Backboard,
    content: str,
    *,
    tools: Sequence[ToolDefinition | dict[str, Any]],
    registry: ToolRegistry,
    thread_id: Uuid | None = None,
    assistant_id: Uuid | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    **send_kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming loop: drop-in replacement for `stream_message` that also
    executes tools. Yields every SSE event unchanged (the caller renders
    content_streaming/reasoning_* itself and sees each round's
    tool_submit_required and run_ended); between rounds it runs the requested
    tools and continues on stream_tool_outputs until a stream ends with no
    pending calls."""
    stream = bb.stream_message(
        content,
        thread_id=thread_id,
        assistant_id=assistant_id,
        tools=list(tools),
        **send_kwargs,
    )
    rounds = 0
    while True:
        pending: list[ToolCall | dict[str, Any]] | None = None
        async for event in stream:
            yield event
            if event.get("type") == "tool_submit_required":
                pending = _extract_stream_tool_calls(event)
                if not pending:
                    raise ExecutorError(
                        "tool_submit_required event carried no tool calls"
                    )
            # Auto-created threads: the caller may not know the thread id, so
            # pick it up from any event that carries one.
            thread_id = event.get("thread_id") or thread_id
        if pending is None:
            return
        rounds += 1
        if rounds > max_rounds:
            raise MaxRoundsExceeded(str(thread_id), rounds)
        if thread_id is None:
            raise ExecutorError(
                "tool_submit_required but no thread_id observed in stream"
            )
        outputs = await execute_tool_calls(registry, pending)
        stream = bb.stream_tool_outputs(thread_id, outputs)


def final_text(response: ChatMessagesResponse) -> str:
    return response.content or ""
