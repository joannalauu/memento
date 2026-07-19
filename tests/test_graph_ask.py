"""Tests for the web Q&A SSE transport (app/graph/ask.py): frame mapping,
bb/traversal interleaving, disconnect cleanup, error codes, and route guards.

The generator is exercised directly (no HTTP server): fake `stream_with_tools`
generators stand in for Backboard, and traversal events go through the real
`traversal_channel` under unique session ids."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backboard import BackboardAPIError
from beanie import PydanticObjectId
from fastapi import HTTPException

import app.graph.ask as ask_mod
from app.backboard.executor import ExecutorError, MaxRoundsExceeded
from app.graph.ask import AskRequest, _ask_stream, ask_graph_endpoint
from app.graph.qa_tools import CitationCollector
from app.traversal import TraversalTag, traversal_channel


def _parse(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") : -2])


def _stream_kwargs(session_id: str, citations: CitationCollector | None = None):
    return dict(
        tools=[],
        registry={},
        citations=citations or CitationCollector(),
        assistant_id="asst-1",
        session_id=session_id,
        system_prompt="sp",
    )


async def _frames(gen) -> list[dict]:
    return [_parse(f) async for f in gen]


# ─── frame mapping ────────────────────────────────────────────────────────────


async def test_happy_path_content_then_done(monkeypatch):
    async def fake_stream(bb, question, **kwargs):
        yield {"type": "content_streaming", "content": "hello "}
        yield {"type": "reasoning_streaming", "content": "hmm"}  # internal
        yield {"type": "content_streaming", "content": "world"}
        yield {"type": "run_ended", "status": "completed"}  # internal

    monkeypatch.setattr(ask_mod, "stream_with_tools", fake_stream)
    citations = CitationCollector()
    citations.add("dec:a", 12)
    sid = uuid4().hex

    frames = await _frames(
        _ask_stream(SimpleNamespace(), "q", **_stream_kwargs(sid, citations))
    )
    assert frames == [
        {"type": "content_delta", "content": "hello "},
        {"type": "content_delta", "content": "world"},
        {"type": "done", "citations": [{"nodeId": "dec:a", "prNumber": 12}]},
    ]


async def test_traversal_events_interleave_in_order(monkeypatch):
    sid = uuid4().hex
    tag = TraversalTag(sid, "web")

    async def fake_stream(bb, question, **kwargs):
        yield {"type": "content_streaming", "content": "a"}
        # Emitted "during tool execution": lands on the queue between the two
        # content deltas, exactly as the channel publishes mid-round.
        traversal_channel.publish(
            tag, kind="entry", node_id="dec:x", edge_kind=None, from_node_id=None
        )
        traversal_channel.publish(
            tag,
            kind="hop",
            node_id="pr:o/r:5",
            edge_kind="introduced",
            from_node_id="dec:x",
        )
        yield {"type": "content_streaming", "content": "b"}

    monkeypatch.setattr(ask_mod, "stream_with_tools", fake_stream)
    frames = await _frames(_ask_stream(SimpleNamespace(), "q", **_stream_kwargs(sid)))
    assert frames == [
        {"type": "content_delta", "content": "a"},
        {"type": "tool_activity", "nodeId": "dec:x", "edgeKind": None, "kind": "entry"},
        {
            "type": "tool_activity",
            "nodeId": "pr:o/r:5",
            "edgeKind": "introduced",
            "kind": "hop",
        },
        {"type": "content_delta", "content": "b"},
        {"type": "done", "citations": []},
    ]


async def test_other_sessions_events_not_forwarded(monkeypatch):
    sid = uuid4().hex

    async def fake_stream(bb, question, **kwargs):
        traversal_channel.publish(
            TraversalTag(uuid4().hex, "web"),  # someone else's session
            kind="entry",
            node_id="dec:other",
            edge_kind=None,
            from_node_id=None,
        )
        yield {"type": "content_streaming", "content": "a"}

    monkeypatch.setattr(ask_mod, "stream_with_tools", fake_stream)
    frames = await _frames(_ask_stream(SimpleNamespace(), "q", **_stream_kwargs(sid)))
    assert [f["type"] for f in frames] == ["content_delta", "done"]


# ─── disconnect cleanup ───────────────────────────────────────────────────────


async def test_disconnect_unsubscribes_and_cancels_pump(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_stream(bb, question, **kwargs):
        yield {"type": "content_streaming", "content": "x"}
        started.set()
        try:
            await asyncio.Event().wait()  # hang like a stalled upstream
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(ask_mod, "stream_with_tools", fake_stream)
    sid = uuid4().hex
    gen = _ask_stream(SimpleNamespace(), "q", **_stream_kwargs(sid))

    first = _parse(await anext(gen))
    assert first == {"type": "content_delta", "content": "x"}
    assert sid in traversal_channel._subscribers
    await started.wait()

    await gen.aclose()  # what Starlette does on client disconnect

    assert cancelled.is_set()
    assert sid not in traversal_channel._subscribers


# ─── error mapping ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "code", "message_contains"),
    [
        (MaxRoundsExceeded("t1", 11), "max_rounds_exceeded", "rounds"),
        (ExecutorError("carried no tool calls"), "executor_error", "tool calls"),
        (BackboardAPIError("run failed"), "backboard_error", "run failed"),
        (RuntimeError("secret detail"), "internal", "internal error"),
    ],
)
async def test_error_frames(monkeypatch, exc, code, message_contains):
    async def fake_stream(bb, question, **kwargs):
        raise exc
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(ask_mod, "stream_with_tools", fake_stream)
    sid = uuid4().hex

    frames = await _frames(_ask_stream(SimpleNamespace(), "q", **_stream_kwargs(sid)))
    assert len(frames) == 1
    assert frames[0]["type"] == "error"
    assert frames[0]["code"] == code
    assert message_contains in frames[0]["message"]
    if code == "internal":  # tracebacks stay in the log, not on the wire
        assert "secret detail" not in frames[0]["message"]
    assert sid not in traversal_channel._subscribers


# ─── route guards + session id ────────────────────────────────────────────────


USER = SimpleNamespace(id=PydanticObjectId())


def _member_org(**overrides):
    defaults = dict(
        id=PydanticObjectId(),
        slug="acme",
        bbAssistantId="asst-1",
        githubInstallationId=None,
        members=[SimpleNamespace(userId=USER.id)],
    )
    return SimpleNamespace(**{**defaults, **overrides})


async def _call_endpoint(monkeypatch, org, *, tag=None):
    async def fake_get_org(org_id):
        return org

    async def fake_list_repos(org_id):
        return []

    captured = {}

    def fake_build(*, bb, gh, org, repos, session_id):
        captured["session_id"] = session_id
        return [], {}, CitationCollector()

    monkeypatch.setattr(ask_mod, "get_org", fake_get_org)
    monkeypatch.setattr(ask_mod, "list_repos_for_org", fake_list_repos)
    monkeypatch.setattr(ask_mod, "build_qa_toolset", fake_build)
    response = await ask_graph_endpoint(
        PydanticObjectId(),
        AskRequest(question="why?"),
        user=USER,
        tag=tag,
        bb=SimpleNamespace(),
        gh=SimpleNamespace(),
    )
    return response, captured


async def test_unknown_org_404(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        await _call_endpoint(monkeypatch, None)
    assert exc_info.value.status_code == 404


async def test_non_member_403(monkeypatch):
    org = _member_org(members=[SimpleNamespace(userId=PydanticObjectId())])
    with pytest.raises(HTTPException) as exc_info:
        await _call_endpoint(monkeypatch, org)
    assert exc_info.value.status_code == 403


async def test_session_id_from_header_tag(monkeypatch):
    response, captured = await _call_endpoint(
        monkeypatch, _member_org(), tag=TraversalTag("sess-abc", "web")
    )
    assert captured["session_id"] == "sess-abc"
    assert response.headers["X-Session-Id"] == "sess-abc"
    assert response.media_type == "text/event-stream"


async def test_session_id_minted_when_no_header(monkeypatch):
    response, captured = await _call_endpoint(monkeypatch, _member_org(), tag=None)
    minted = captured["session_id"]
    assert len(minted) == 32 and all(c in "0123456789abcdef" for c in minted)
    assert response.headers["X-Session-Id"] == minted
