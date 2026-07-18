import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.context_engine.schemas import StalenessVerdict
from app.gap_chat import service
from app.gap_chat.service import build_question, open_gap_chat, submit_answer


def make_memory(
    *,
    source="legacy_doc",
    confidence="unverified",
    content="[repo: acme/api] tokens live in middleware",
    files=("app/auth.py",),
    symbols=(),
):
    return SimpleNamespace(
        id=PydanticObjectId(),
        bbMemoryId="mem-old",
        orgId=PydanticObjectId(),
        repoId=PydanticObjectId(),
        source=source,
        confidence=confidence,
        contentSnapshot=content,
        feature="auth",
        anchors=SimpleNamespace(
            repo="acme/api", files=list(files), symbols=list(symbols)
        ),
        commitSha="old-sha",
        stalenessStatus="gap",
        stalenessCheckedAt=None,
        supersededBy=None,
        archivedContent=None,
        deletedAt=None,
        save=AsyncMock(),
    )


def verdict(status="gap", files=("app/auth.py",)):
    return StalenessVerdict(
        status=status,
        memoryCommitSha="old-sha",
        currentShaCheckedAt="2026-07-18T00:00:00+00:00",
        changedFiles=list(files),
        commitsSince=2,
        newerMemoryExists=False,
    )


def make_chat(**over):
    base = dict(
        id=PydanticObjectId(),
        orgId=PydanticObjectId(),
        repoId=PydanticObjectId(),
        bbMemoryId="mem-old",
        bbThreadId="thread-1",
        memoryContent="[repo: acme/api] tokens live in middleware",
        changedFiles=["app/auth.py"],
        triggerCommitSha="new-sha",
        triggerStatus="gap",
        messages=[SimpleNamespace(role="assistant", text="is it still accurate?")],
        status="open",
        supersededByMemoryId=None,
        resolvedByUserId=None,
        resolvedAt=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def org():
    return SimpleNamespace(id=PydanticObjectId(), bbAssistantId="assist")


@pytest.fixture
def patch_crud(monkeypatch):
    """Patch the DB-touching crud calls; append_message really appends."""
    monkeypatch.setattr(
        service.crud, "get_open_chat_for_memory", AsyncMock(return_value=None)
    )
    created = AsyncMock(side_effect=lambda **kw: make_chat(**_chat_from_create(kw)))
    monkeypatch.setattr(service.crud, "create_gap_chat", created)

    async def _append(chat, role, text):
        chat.messages.append(SimpleNamespace(role=role, text=text))
        return chat

    monkeypatch.setattr(service.crud, "append_message", AsyncMock(side_effect=_append))
    return created


def _chat_from_create(kw):
    return dict(
        bbMemoryId=kw["bb_memory_id"],
        memoryContent=kw["memory_content"],
        changedFiles=kw["changed_files"],
        triggerCommitSha=kw["trigger_commit_sha"],
        triggerStatus=kw["trigger_status"],
        bbThreadId=kw["bb_thread_id"],
        messages=[SimpleNamespace(role="assistant", text=kw["question"])],
    )


# ─── build_question ───────────────────────────────────────────────────────────


def test_build_question_strips_repo_prefix_and_names_files():
    q = build_question("[repo: acme/api] use asyncpg", ["app/db.py", "app/pool.py"])
    assert "use asyncpg" in q
    assert "[repo:" not in q
    assert "app/db.py" in q


# ─── open_gap_chat eligibility ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "memory,vd",
    [
        (make_memory(source="claude_session"), verdict("gap")),  # not legacy
        (make_memory(confidence="verified"), verdict("gap")),  # already verified
        (make_memory(), verdict("fresh")),  # not stale
    ],
)
async def test_open_gap_chat_skips_ineligible(memory, vd, org):
    bb = SimpleNamespace(create_thread=AsyncMock())
    assert (
        await open_gap_chat(memory, vd, org=org, bb=bb, trigger_commit_sha="s") is None
    )
    bb.create_thread.assert_not_called()


async def test_open_gap_chat_creates_chat_with_thread_and_question(patch_crud, org):
    bb = SimpleNamespace(
        create_thread=AsyncMock(return_value=SimpleNamespace(id="t-9"))
    )
    memory = make_memory()
    chat = await open_gap_chat(
        memory,
        verdict("gap"),
        org=org,
        bb=bb,
        trigger_commit_sha="new-sha",
        pr_number=7,
    )
    assert chat is not None
    _, kwargs = patch_crud.call_args
    assert kwargs["bb_memory_id"] == "mem-old"
    assert kwargs["bb_thread_id"] == "t-9"
    assert kwargs["trigger_commit_sha"] == "new-sha"
    assert "tokens live in middleware" in kwargs["question"]


async def test_open_gap_chat_is_idempotent(monkeypatch, org):
    existing = make_chat()
    monkeypatch.setattr(
        service.crud, "get_open_chat_for_memory", AsyncMock(return_value=existing)
    )
    create = AsyncMock()
    monkeypatch.setattr(service.crud, "create_gap_chat", create)
    bb = SimpleNamespace(create_thread=AsyncMock())
    got = await open_gap_chat(
        make_memory(), verdict(), org=org, bb=bb, trigger_commit_sha="s"
    )
    assert got is existing
    create.assert_not_called()
    bb.create_thread.assert_not_called()


async def test_open_gap_chat_survives_thread_failure(patch_crud, org):
    bb = SimpleNamespace(create_thread=AsyncMock(side_effect=RuntimeError("down")))
    chat = await open_gap_chat(
        make_memory(), verdict(), org=org, bb=bb, trigger_commit_sha="s"
    )
    assert chat is not None
    _, kwargs = patch_crud.call_args
    assert kwargs["bb_thread_id"] is None  # thread failed, chat still opened


# ─── submit_answer outcomes ───────────────────────────────────────────────────


def _bb_classify(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(content=json.dumps(payload))
        )
    )


async def test_answer_verified_upgrades_and_rebaselines(monkeypatch, patch_crud, org):
    memory = make_memory()
    monkeypatch.setattr(
        service.MemoryIndex, "find_one", staticmethod(AsyncMock(return_value=memory))
    )
    bb = _bb_classify({"resolution": "verified", "statement": None, "reasoning": "ok"})
    bb.add_memory = AsyncMock()
    chat = make_chat()

    result = await submit_answer(
        chat, "yes still true", org=org, bb=bb, author_user_id="u1"
    )

    assert result.status == "verified"
    assert memory.confidence == "verified"
    assert memory.commitSha == "new-sha"  # re-baselined to the trigger commit
    assert memory.stalenessStatus == "fresh"
    memory.save.assert_awaited_once()
    bb.add_memory.assert_not_called()  # verify never writes a new memory


async def test_answer_superseded_writes_new_and_retires_old(
    monkeypatch, patch_crud, org
):
    memory = make_memory()
    monkeypatch.setattr(
        service.MemoryIndex, "find_one", staticmethod(AsyncMock(return_value=memory))
    )
    new_id = PydanticObjectId()
    bb = _bb_classify(
        {
            "resolution": "superseded",
            "statement": "tokens now validated per-route",
            "reasoning": "x",
        }
    )
    bb.add_memory = AsyncMock(
        return_value=SimpleNamespace(id=new_id, bbMemoryId="mem-new")
    )
    bb.sdk = SimpleNamespace(delete_memory=AsyncMock())
    chat = make_chat()

    result = await submit_answer(
        chat, "no, changed", org=org, bb=bb, author_user_id="u1"
    )

    assert result.status == "superseded"
    assert result.supersededByMemoryId == "mem-new"
    # new memory written verified, on the trigger baseline, same anchors
    _, kw = bb.add_memory.call_args
    assert kw["source"] == "legacy_doc" and kw["confidence"] == "verified"
    assert kw["commit_sha"] == "new-sha"
    assert kw["content"] == "tokens now validated per-route"
    assert kw["files"] == ["app/auth.py"]
    # old removed from Backboard + lineage recorded on the retired doc
    bb.sdk.delete_memory.assert_awaited_once_with("assist", "mem-old")
    assert memory.supersededBy == new_id
    assert memory.deletedAt is not None


async def test_answer_superseded_without_statement_falls_back_to_answer(
    monkeypatch, patch_crud, org
):
    memory = make_memory()
    monkeypatch.setattr(
        service.MemoryIndex, "find_one", staticmethod(AsyncMock(return_value=memory))
    )
    bb = _bb_classify({"resolution": "superseded", "statement": None, "reasoning": "x"})
    bb.add_memory = AsyncMock(
        return_value=SimpleNamespace(id=PydanticObjectId(), bbMemoryId="m2")
    )
    bb.sdk = SimpleNamespace(delete_memory=AsyncMock())
    chat = make_chat()

    await submit_answer(chat, "it moved to per-route checks", org=org, bb=bb)
    _, kw = bb.add_memory.call_args
    assert kw["content"] == "it moved to per-route checks"  # answer used as statement


async def test_answer_unclassifiable_leaves_chat_open(monkeypatch, patch_crud, org):
    find = AsyncMock()
    monkeypatch.setattr(service.MemoryIndex, "find_one", staticmethod(find))
    bb = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(content="not json"))
    )
    chat = make_chat()
    result = await submit_answer(chat, "hmm", org=org, bb=bb)
    assert result.status == "open"  # nothing mutated
    find.assert_not_called()  # never reached the memory lookup


async def test_answer_when_memory_gone_dismisses(monkeypatch, patch_crud, org):
    monkeypatch.setattr(
        service.MemoryIndex, "find_one", staticmethod(AsyncMock(return_value=None))
    )
    bb = _bb_classify({"resolution": "verified", "statement": None, "reasoning": "ok"})
    chat = make_chat()
    result = await submit_answer(chat, "yes", org=org, bb=bb)
    assert result.status == "dismissed"
