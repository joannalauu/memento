from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.backboard.client import Backboard, BackboardSettings
from app.backboard.models import MemoryIndex


@pytest.fixture(autouse=True)
def stub_beanie(monkeypatch):
    # Document.__init__ only calls get_pymongo_collection() as an
    # "init_beanie has run" guard; stub it so tests need no Mongo.
    monkeypatch.setattr(
        MemoryIndex, "get_pymongo_collection", classmethod(lambda cls: None)
    )


@pytest.fixture
def bb():
    wrapper = Backboard(BackboardSettings(api_key="test-key"))
    wrapper._client = AsyncMock()
    return wrapper


@pytest.fixture
def captured_insert(monkeypatch):
    inserted: list[MemoryIndex] = []

    async def fake_insert(self):
        inserted.append(self)
        return self

    monkeypatch.setattr(MemoryIndex, "insert", fake_insert)
    return inserted


async def test_add_memory_injects_repo_and_writes_index(bb, captured_insert):
    bb._client.add_memory.return_value = {"success": True, "memory_id": "mem-123"}

    index = await bb.add_memory(
        assistant_id="assistant-1",
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        repo="acme/api-server",
        content="Team chose token-bucket rate limiting",
        metadata={"repo": "evil/override", "category": "decision"},
        source="form",
        confidence="verified",
        files=["app/rate_limit.py"],
        symbols=["RateLimiter"],
    )

    args = bb._client.add_memory.await_args.args
    assert args[0] == "assistant-1"
    assert args[1] == "[repo: acme/api-server] Team chose token-bucket rate limiting"
    # repo always wins over caller-supplied metadata
    assert args[2] == {"category": "decision", "repo": "acme/api-server"}

    assert captured_insert == [index]
    assert index.bbMemoryId == "mem-123"
    assert index.contentSnapshot == args[1]
    assert index.source == "form"
    assert index.confidence == "verified"
    assert index.anchors.repo == "acme/api-server"
    assert index.anchors.files == ["app/rate_limit.py"]
    assert index.anchors.symbols == ["RateLimiter"]
    assert index.deletedAt is None


async def test_add_memory_without_memory_id_raises_and_skips_index(bb, captured_insert):
    bb._client.add_memory.return_value = {"success": False}

    with pytest.raises(RuntimeError, match="no memory_id"):
        await bb.add_memory(
            assistant_id="assistant-1",
            org_id=PydanticObjectId(),
            repo_id=PydanticObjectId(),
            repo="acme/api-server",
            content="whatever",
        )
    assert captured_insert == []


async def test_update_memory_reinjects_repo_and_refreshes_snapshot(bb, monkeypatch):
    doc = MemoryIndex.model_construct(bbMemoryId="mem-123", contentSnapshot="old")
    saved = []

    async def fake_save(self, *a, **k):
        saved.append(self)
        return self

    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=doc))
    monkeypatch.setattr(MemoryIndex, "save", fake_save)

    await bb.update_memory(
        assistant_id="assistant-1",
        memory_id="mem-123",
        repo="acme/api-server",
        content="Revised decision",
    )

    args = bb._client.update_memory.await_args.args
    assert args[2] == "[repo: acme/api-server] Revised decision"
    assert args[3] == {"repo": "acme/api-server"}
    assert doc.contentSnapshot == "[repo: acme/api-server] Revised decision"
    assert saved == [doc]


async def test_delete_memory_soft_deletes_index(bb, monkeypatch):
    doc = MemoryIndex.model_construct(bbMemoryId="mem-123", deletedAt=None)
    saved = []

    async def fake_save(self, *a, **k):
        saved.append(self)
        return self

    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=doc))
    monkeypatch.setattr(MemoryIndex, "save", fake_save)
    bb._client.delete_memory.return_value = {"success": True}

    result = await bb.delete_memory("assistant-1", "mem-123")

    assert result == {"success": True}
    assert doc.deletedAt is not None
    assert saved == [doc]


async def test_send_message_is_non_streaming(bb):
    sentinel = object()
    bb._client.send_message.return_value = sentinel

    response = await bb.send_message("hello", assistant_id="assistant-1", memory="Auto")

    assert response is sentinel
    kwargs = bb._client.send_message.await_args.kwargs
    assert kwargs["stream"] is False
    assert kwargs["memory"] == "Auto"


async def test_stream_message_yields_sse_events(bb):
    async def events():
        yield {"type": "content_streaming", "content": "Par"}
        yield {"type": "run_ended", "status": "COMPLETED"}

    bb._client.send_message.return_value = events()

    chunks = [event async for event in bb.stream_message("hello", assistant_id="assistant-1")]

    assert [c["type"] for c in chunks] == ["content_streaming", "run_ended"]
    assert bb._client.send_message.await_args.kwargs["stream"] is True


async def test_list_threads_dispatches_on_assistant_id(bb):
    await bb.list_threads()
    bb._client.list_threads.assert_awaited_once_with(skip=0, limit=100)

    await bb.list_threads(assistant_id="assistant-1", limit=10)
    bb._client.list_threads_for_assistant.assert_awaited_once_with("assistant-1", skip=0, limit=10)
