from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from backboard.models import ChatMessagesResponse, MemoryOperationStatus

from app.backboard.client import Backboard, BackboardSettings, _extract_transcript
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
    assert index.stalenessStatus == "fresh"
    assert index.stalenessCheckedAt is not None


async def test_add_memory_supersedes_flips_old_to_stale(
    bb, captured_insert, monkeypatch
):
    bb._client.add_memory.return_value = {"success": True, "memory_id": "mem-new"}
    old = MemoryIndex.model_construct(
        bbMemoryId="mem-old", contentSnapshot="old", stalenessStatus="fresh"
    )
    saved = []

    async def fake_save(self, *a, **k):
        saved.append(self)
        return self

    monkeypatch.setattr(MemoryIndex, "get", AsyncMock(return_value=old))
    monkeypatch.setattr(MemoryIndex, "save", fake_save)

    index = await bb.add_memory(
        assistant_id="assistant-1",
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        repo="acme/api-server",
        content="Switched to sliding-window rate limiting",
        supersedes=[PydanticObjectId()],
    )

    assert old.supersededBy == index.id
    assert old.stalenessStatus == "stale"
    assert old.stalenessCheckedAt is not None
    assert saved == [old]


async def test_add_memory_supersedes_skips_missing_id(bb, captured_insert, monkeypatch):
    bb._client.add_memory.return_value = {"success": True, "memory_id": "mem-new"}
    monkeypatch.setattr(MemoryIndex, "get", AsyncMock(return_value=None))
    save_mock = AsyncMock()
    monkeypatch.setattr(MemoryIndex, "save", save_mock)

    await bb.add_memory(
        assistant_id="assistant-1",
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        repo="acme/api-server",
        content="whatever",
        supersedes=[PydanticObjectId()],
    )

    save_mock.assert_not_awaited()


def _op_status(state):
    return MemoryOperationStatus(operation_id="op-1", status=state)


async def test_add_memory_polls_operation_before_mirror(
    bb, captured_insert, monkeypatch
):
    bb._client.add_memory.return_value = {
        "success": True,
        "memory_id": "mem-async",
        "memory_operation_id": "op-1",
    }
    bb._client.get_memory_operation_status.side_effect = [
        _op_status("PENDING"),
        _op_status("COMPLETED"),
    ]
    monkeypatch.setattr("app.backboard.client.asyncio.sleep", AsyncMock())

    index = await bb.add_memory(
        assistant_id="assistant-1",
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        repo="acme/api-server",
        content="async write",
    )

    assert bb._client.get_memory_operation_status.await_count == 2
    assert captured_insert == [index]  # mirror written only after completion


async def test_add_memory_operation_failure_raises_and_skips_mirror(
    bb, captured_insert
):
    bb._client.add_memory.return_value = {
        "success": True,
        "memory_id": "mem-async",
        "operation_id": "op-1",
    }
    bb._client.get_memory_operation_status.return_value = _op_status("FAILED")

    with pytest.raises(RuntimeError, match="operation op-1 failed"):
        await bb.add_memory(
            assistant_id="assistant-1",
            org_id=PydanticObjectId(),
            repo_id=PydanticObjectId(),
            repo="acme/api-server",
            content="async write",
        )
    assert captured_insert == []


async def test_add_memory_without_operation_id_skips_polling(bb, captured_insert):
    bb._client.add_memory.return_value = {"success": True, "memory_id": "mem-sync"}

    await bb.add_memory(
        assistant_id="assistant-1",
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        repo="acme/api-server",
        content="sync write",
    )

    bb._client.get_memory_operation_status.assert_not_awaited()


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

    chunks = [
        event async for event in bb.stream_message("hello", assistant_id="assistant-1")
    ]

    assert [c["type"] for c in chunks] == ["content_streaming", "run_ended"]
    assert bb._client.send_message.await_args.kwargs["stream"] is True


async def test_list_threads_dispatches_on_assistant_id(bb):
    await bb.list_threads()
    bb._client.list_threads.assert_awaited_once_with(skip=0, limit=100)

    await bb.list_threads(assistant_id="assistant-1", limit=10)
    bb._client.list_threads_for_assistant.assert_awaited_once_with(
        "assistant-1", skip=0, limit=10
    )


# ─── speech-to-text ───────────────────────────────────────────────────────────


def _msg_response(message: dict) -> ChatMessagesResponse:
    return ChatMessagesResponse(messages=[message])


def test_extract_transcript_prefers_voice_records_transcript():
    r = _msg_response(
        {"voice_records": {"transcript": "  hello world  "}, "content": "x"}
    )
    assert _extract_transcript(r) == "hello world"


def test_extract_transcript_reads_nested_stt():
    r = _msg_response({"voice_records": {"stt": {"transcript": "nested"}}})
    assert _extract_transcript(r) == "nested"


def test_extract_transcript_falls_back_to_content():
    r = _msg_response({"voice_records": {}, "content": "fallback text"})
    assert _extract_transcript(r) == "fallback text"


def test_extract_transcript_empty_when_nothing_usable():
    assert (
        _extract_transcript(_msg_response({"voice_records": {}, "content": "  "})) == ""
    )
    assert _extract_transcript(ChatMessagesResponse(messages=[])) == ""


async def test_transcribe_audio_uses_stt_config_and_no_llm(bb):
    bb._client.add_message.return_value = _msg_response(
        {"voice_records": {"transcript": "spoken answer"}}
    )
    out = await bb.transcribe_audio(thread_id="t-1", audio_path="/tmp/a.webm")

    assert out == "spoken answer"
    args, kwargs = bb._client.add_message.call_args
    assert args[0] == "t-1"  # thread-scoped
    assert kwargs["audio_file"] == "/tmp/a.webm"
    assert kwargs["send_to_llm"] == "false"  # transcribe only, no model reply
    assert kwargs["voice"] == {"stt": {"provider": "elevenlabs", "model": "scribe_v1"}}


async def test_transcribe_audio_honors_settings_override():
    wrapper = Backboard(
        BackboardSettings(api_key="k", stt_provider="whisper", stt_model="large-v3")
    )
    wrapper._client = AsyncMock()
    wrapper._client.add_message.return_value = _msg_response({"content": "hi"})
    await wrapper.transcribe_audio(thread_id="t", audio_path="/tmp/a.m4a")
    kwargs = wrapper._client.add_message.call_args.kwargs
    assert kwargs["voice"] == {"stt": {"provider": "whisper", "model": "large-v3"}}
