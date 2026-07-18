"""Tests for the coverage-gap staleness enrichment (_staleness_flags_for_gap
and _anchored_memories) — the "make the gap visible not silent" path."""

from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine.schemas import StalenessVerdict
from app.distillation import pipeline
from app.job_queue.models import PipelineJob
from app.orgs.models import Repo

ORG_ID = PydanticObjectId()
REPO_ID = PydanticObjectId()

DIFF = """--- app/limits.py (modified, +10/-2)
@@ -1,4 +8,6 @@ def rate_limit
+def rate_limit(user):
"""


def make_job(**over):
    fields = dict(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        prNumber=7,
        headSha="abc123",
        headBranch="feat/x",
        baseBranch="main",
        authorUserId=None,
        prAuthorGithub="someone",
        deliveryId="d-1",
        installationId=42,
    )
    fields.update(over)
    return PipelineJob.model_construct(**fields)


def make_repo(**over):
    fields = dict(
        id=REPO_ID,
        orgId=ORG_ID,
        githubRepoId=123,
        owner="acme",
        name="api",
        defaultBranch="main",
        active=True,
    )
    fields.update(over)
    return Repo.model_construct(**fields)


def make_memory(bb_id, files=("app/limits.py",), **over):
    fields = dict(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        bbMemoryId=bb_id,
        contentSnapshot=f"content {bb_id}",
        anchors=Anchors(repo="acme/api", files=list(files)),
        commitSha="old-sha",
    )
    fields.update(over)
    return MemoryIndex.model_construct(**fields)


def verdict(status):
    return StalenessVerdict(
        status=status,
        memoryCommitSha="old-sha",
        currentShaCheckedAt="2026-07-18T00:00:00+00:00",
        changedFiles=["app/limits.py"] if status != "fresh" else [],
    )


class FakeToolset:
    diff = DIFF

    def __init__(self, gh, **kwargs):
        pass

    async def get_pr_diff(self, pr_number):
        return self.diff


@pytest.fixture(autouse=True)
def stub_history(monkeypatch):
    # RepoHistory is constructed but never really called (staleness_check is
    # mocked in these tests); a no-op stand-in keeps construction cheap.
    monkeypatch.setattr(pipeline, "RepoHistory", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "GitHubToolset", FakeToolset)
    FakeToolset.diff = DIFF


async def test_flags_only_non_fresh(monkeypatch):
    mems = [make_memory("mem-fresh"), make_memory("mem-gap"), make_memory("mem-stale")]
    monkeypatch.setattr(pipeline, "_anchored_memories", AsyncMock(return_value=mems))

    async def fake_check(memory, **kwargs):
        return verdict(
            {"mem-fresh": "fresh", "mem-gap": "gap", "mem-stale": "stale"}[
                memory.bbMemoryId
            ]
        )

    monkeypatch.setattr(pipeline, "staleness_check", fake_check)

    flags = await pipeline._staleness_flags_for_gap(
        make_job(), make_repo(), gh=AsyncMock()
    )

    assert {f.bbMemoryId for f in flags} == {"mem-gap", "mem-stale"}


async def test_staleness_checks_against_base_branch(monkeypatch):
    monkeypatch.setattr(
        pipeline, "_anchored_memories", AsyncMock(return_value=[make_memory("m")])
    )
    check = AsyncMock(return_value=verdict("gap"))
    monkeypatch.setattr(pipeline, "staleness_check", check)

    await pipeline._staleness_flags_for_gap(
        make_job(baseBranch="release"), make_repo(), gh=AsyncMock()
    )

    _, kwargs = check.call_args
    assert kwargs["ref"] == "release"


async def test_diff_error_yields_no_flags(monkeypatch):
    FakeToolset.diff = "Error: GitHub API error 502"
    check = AsyncMock()
    monkeypatch.setattr(pipeline, "staleness_check", check)

    flags = await pipeline._staleness_flags_for_gap(
        make_job(), make_repo(), gh=AsyncMock()
    )

    assert flags == []
    check.assert_not_called()


async def test_no_anchored_memories_yields_no_flags(monkeypatch):
    monkeypatch.setattr(pipeline, "_anchored_memories", AsyncMock(return_value=[]))
    check = AsyncMock()
    monkeypatch.setattr(pipeline, "staleness_check", check)

    flags = await pipeline._staleness_flags_for_gap(
        make_job(), make_repo(), gh=AsyncMock()
    )

    assert flags == []
    check.assert_not_called()


async def test_enrichment_never_raises(monkeypatch):
    monkeypatch.setattr(
        pipeline, "_anchored_memories", AsyncMock(return_value=[make_memory("m")])
    )
    monkeypatch.setattr(
        pipeline, "staleness_check", AsyncMock(side_effect=RuntimeError("boom"))
    )

    # a broken staleness check degrades to no flags, not a job failure
    flags = await pipeline._staleness_flags_for_gap(
        make_job(), make_repo(), gh=AsyncMock()
    )

    assert flags == []


async def test_respects_memory_cap(monkeypatch):
    mems = [make_memory(f"m{i}") for i in range(pipeline.STALENESS_MEMORY_CAP + 5)]
    monkeypatch.setattr(pipeline, "_anchored_memories", AsyncMock(return_value=mems))
    check = AsyncMock(return_value=verdict("fresh"))
    monkeypatch.setattr(pipeline, "staleness_check", check)

    await pipeline._staleness_flags_for_gap(make_job(), make_repo(), gh=AsyncMock())

    assert check.await_count == pipeline.STALENESS_MEMORY_CAP


# --- _anchored_memories query ------------------------------------------------


async def test_anchored_memories_empty_anchors_skips_query(monkeypatch):
    called = False

    def fake_find(cls, *a, **k):
        nonlocal called
        called = True
        raise AssertionError("should not query")

    monkeypatch.setattr(MemoryIndex, "find", classmethod(fake_find))

    result = await pipeline._anchored_memories(REPO_ID, Anchors(repo="acme/api"))

    assert result == []
    assert called is False


async def test_anchored_memories_queries_files_and_symbols(monkeypatch):
    captured = {}

    class FakeQuery:
        async def to_list(self):
            return [make_memory("m")]

    def fake_find(cls, filter_dict):
        captured["filter"] = filter_dict
        return FakeQuery()

    monkeypatch.setattr(MemoryIndex, "find", classmethod(fake_find))

    result = await pipeline._anchored_memories(
        REPO_ID, Anchors(repo="acme/api", files=["app/limits.py"], symbols=["RL"])
    )

    assert len(result) == 1
    assert captured["filter"]["repoId"] == REPO_ID
    assert captured["filter"]["deletedAt"] is None
    assert {"anchors.files": {"$in": ["app/limits.py"]}} in captured["filter"]["$or"]
    assert {"anchors.symbols": {"$in": ["RL"]}} in captured["filter"]["$or"]
