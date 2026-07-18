"""Executor loop tests — Backboard mocked, responses are REAL SDK models so the
.status/.tool_calls/.run_id properties exercise the actual contract."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from backboard.models import ChatMessagesResponse

from app.backboard.client import Backboard
from app.backboard.executor import (
    ExecutorError,
    MaxRoundsExceeded,
    run_with_tools,
    stream_with_tools,
)

THREAD_ID = "thread-1"
TOOLS = [{"type": "function", "function": {"name": "get_file", "parameters": {}}}]


def requires_action(*calls: tuple[str, str, dict]) -> ChatMessagesResponse:
    """calls: (call_id, name, args)"""
    return ChatMessagesResponse(
        messages=[
            {
                "status": "REQUIRES_ACTION",
                "thread_id": THREAD_ID,
                "run_id": "run-1",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                    for call_id, name, args in calls
                ],
            }
        ]
    )


def completed(text: str = "final answer") -> ChatMessagesResponse:
    return ChatMessagesResponse(
        messages=[{"status": "COMPLETED", "thread_id": THREAD_ID, "content": text}]
    )


# ─── Non-streaming ────────────────────────────────────────────────────────────


async def test_run_with_tools_multi_round():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = requires_action(("c1", "get_file", {"path": "a.py"}))
    bb.submit_tool_outputs.side_effect = [
        requires_action(("c2", "get_blame", {"path": "a.py", "start": 1, "end": 2})),
        completed(),
    ]
    executed = []

    async def get_file(args):
        executed.append(("get_file", args))
        return "file body"

    async def get_blame(args):
        executed.append(("get_blame", args))
        return "blame body"

    response = await run_with_tools(
        bb,
        "hi",
        tools=TOOLS,
        registry={"get_file": get_file, "get_blame": get_blame},
    )
    assert response.content == "final answer"
    assert bb.submit_tool_outputs.await_count == 2
    assert executed == [
        ("get_file", {"path": "a.py"}),
        ("get_blame", {"path": "a.py", "start": 1, "end": 2}),
    ]
    first_call = bb.submit_tool_outputs.await_args_list[0]
    assert first_call.args[0] == THREAD_ID
    assert first_call.args[1] == [{"tool_call_id": "c1", "output": "file body"}]


async def test_parallel_tool_calls_single_submit():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = requires_action(
        ("c1", "slow", {}), ("c2", "fast", {})
    )
    bb.submit_tool_outputs.return_value = completed()

    async def slow(args):
        await asyncio.sleep(0.05)
        return "slow out"

    async def fast(args):
        await asyncio.sleep(0.05)
        return "fast out"

    start = time.monotonic()
    await run_with_tools(bb, "hi", tools=TOOLS, registry={"slow": slow, "fast": fast})
    elapsed = time.monotonic() - start
    assert elapsed < 0.09  # ran concurrently, not 0.05 + 0.05

    bb.submit_tool_outputs.assert_awaited_once()
    outputs = bb.submit_tool_outputs.await_args.args[1]
    assert outputs == [
        {"tool_call_id": "c1", "output": "slow out"},
        {"tool_call_id": "c2", "output": "fast out"},
    ]


async def test_tool_error_becomes_error_output():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = requires_action(("c1", "boom", {}))
    bb.submit_tool_outputs.return_value = completed()

    async def boom(args):
        raise ValueError("kaput")

    response = await run_with_tools(bb, "hi", tools=TOOLS, registry={"boom": boom})
    assert response.status == "COMPLETED"
    output = bb.submit_tool_outputs.await_args.args[1][0]["output"]
    assert output.startswith("Error executing tool 'boom'")
    assert "kaput" in output


async def test_unknown_tool_and_bad_json_args():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = ChatMessagesResponse(
        messages=[
            {
                "status": "REQUIRES_ACTION",
                "thread_id": THREAD_ID,
                "run_id": "run-1",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "ghost", "arguments": "{}"},
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "get_file", "arguments": "{not json"},
                    },
                ],
            }
        ]
    )
    bb.submit_tool_outputs.return_value = completed()

    async def get_file(args):
        raise AssertionError("must not be called with unparseable args")

    await run_with_tools(bb, "hi", tools=TOOLS, registry={"get_file": get_file})
    outputs = bb.submit_tool_outputs.await_args.args[1]
    assert outputs[0]["tool_call_id"] == "c1"
    assert "unknown tool 'ghost'" in outputs[0]["output"]
    assert outputs[1]["tool_call_id"] == "c2"
    assert "could not parse arguments" in outputs[1]["output"]


async def test_max_rounds_cancels_and_raises():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = requires_action(("c1", "t", {}))
    bb.submit_tool_outputs.return_value = requires_action(("c2", "t", {}))

    async def t(args):
        return "out"

    with pytest.raises(MaxRoundsExceeded):
        await run_with_tools(bb, "hi", tools=TOOLS, registry={"t": t}, max_rounds=3)
    assert bb.submit_tool_outputs.await_count == 3
    bb.cancel_run.assert_awaited_once_with(THREAD_ID, "run-1")


async def test_requires_action_without_calls_raises():
    bb = AsyncMock(spec=Backboard)
    bb.send_message.return_value = ChatMessagesResponse(
        messages=[{"status": "REQUIRES_ACTION", "thread_id": THREAD_ID}]
    )
    with pytest.raises(ExecutorError, match="no tool calls"):
        await run_with_tools(bb, "hi", tools=TOOLS, registry={})


# ─── Streaming ────────────────────────────────────────────────────────────────


def make_stream(*events):
    async def gen():
        for e in events:
            yield e

    return gen()


async def test_stream_with_tools_two_rounds():
    bb = AsyncMock(spec=Backboard)
    round1 = [
        {"type": "content_streaming", "content": "thinking...", "thread_id": THREAD_ID},
        {
            "type": "tool_submit_required",
            "thread_id": THREAD_ID,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_file", "arguments": '{"path": "a.py"}'},
                },
            ],
        },
    ]
    round2 = [
        {"type": "content_streaming", "content": "the answer"},
        {"type": "run_ended", "status": "COMPLETED"},
    ]
    bb.stream_message = lambda *a, **k: make_stream(*round1)
    stream_tool_calls = []

    def stream_tool_outputs(thread_id, outputs):
        stream_tool_calls.append((thread_id, outputs))
        return make_stream(*round2)

    bb.stream_tool_outputs = stream_tool_outputs

    async def get_file(args):
        return f"contents of {args['path']}"

    seen = [
        e
        async for e in stream_with_tools(
            bb, "hi", tools=TOOLS, registry={"get_file": get_file}
        )
    ]
    assert seen == round1 + round2  # pure pass-through, order preserved
    assert stream_tool_calls == [
        (THREAD_ID, [{"tool_call_id": "c1", "output": "contents of a.py"}])
    ]


async def test_stream_tool_call_validation_falls_back_to_raw_dict():
    """tool_submit_required's tool_calls are unvalidated by the SDK (see
    _extract_stream_tool_calls) — a payload missing a required ToolCall field
    (here: "type") must still execute via the dict fallback, not raise."""
    bb = AsyncMock(spec=Backboard)
    round1 = [
        {
            "type": "tool_submit_required",
            "thread_id": THREAD_ID,
            "tool_calls": [
                {"id": "c1", "function": {"name": "get_file", "arguments": "{}"}},
            ],
        },
    ]
    round2 = [{"type": "run_ended", "status": "COMPLETED"}]
    bb.stream_message = lambda *a, **k: make_stream(*round1)
    bb.stream_tool_outputs = lambda thread_id, outputs: make_stream(*round2)

    async def get_file(args):
        return "ok"

    seen = [
        e
        async for e in stream_with_tools(
            bb, "hi", tools=TOOLS, registry={"get_file": get_file}
        )
    ]
    assert seen == round1 + round2


async def test_stream_passthrough_no_tools():
    bb = AsyncMock(spec=Backboard)
    events = [
        {"type": "content_streaming", "content": "hello"},
        {"type": "run_ended", "status": "COMPLETED"},
    ]
    bb.stream_message = lambda *a, **k: make_stream(*events)
    bb.stream_tool_outputs = AsyncMock(side_effect=AssertionError("must not be called"))

    seen = [e async for e in stream_with_tools(bb, "hi", tools=TOOLS, registry={})]
    assert seen == events


async def test_stream_max_rounds():
    bb = AsyncMock(spec=Backboard)
    tool_round = [
        {
            "type": "tool_submit_required",
            "thread_id": THREAD_ID,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                },
            ],
        },
    ]
    bb.stream_message = lambda *a, **k: make_stream(*tool_round)
    bb.stream_tool_outputs = lambda thread_id, outputs: make_stream(*tool_round)

    async def t(args):
        return "out"

    with pytest.raises(MaxRoundsExceeded):
        async for _ in stream_with_tools(
            bb, "hi", tools=TOOLS, registry={"t": t}, max_rounds=2
        ):
            pass
