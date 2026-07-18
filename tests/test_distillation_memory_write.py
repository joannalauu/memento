"""Tests for T3.3 — write_distillation and the ensure_features helper.

Monkeypatch style of the rest of the suite: no Mongo, no real Backboard.
`bb` is an AsyncMock; MemoryIndex docs are model_construct'd; job.save and the
MemoryIndex query surface are stubbed.
"""

import copy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine.schemas import ConsistencyConflict
from app.distillation import memory_write
from app.distillation.memory_write import write_distillation
from app.distillation.schemas import (
    DecisionAnchors,
    DistillationResult,
    DistilledDecision,
)
from app.job_queue.models import PipelineJob
from app.orgs.models import Org, Repo

ORG_ID = PydanticObjectId()
REPO_ID = PydanticObjectId()
AUTHOR_ID = PydanticObjectId()


def make_org():
    return Org.model_construct(
        id=ORG_ID, name="Acme", slug="acme", bbAssistantId="asst-1"
    )


def make_repo():
    return Repo.model_construct(
        id=REPO_ID, orgId=ORG_ID, owner="acme", name="api", defaultBranch="main"
    )


def decision(content="d", files=("app/limits.py",), symbols=(), **over):
    fields = dict(
        content=content,
        anchors=DecisionAnchors(files=list(files), symbols=list(symbols)),
        feature="rate-limiting",
        confidence="high",
    )
    fields.update(over)
    return DistilledDecision(**fields)


def make_result(decisions, conflicts=(), **over):
    fields = dict(
        decisions=list(decisions),
        conflicts=list(conflicts),
        sessionIds=[PydanticObjectId()],
        matchMode="branch",
        commitSha="head-sha",
        distilledAt=datetime.now(timezone.utc),
    )
    fields.update(over)
    return DistillationResult(**fields)


def make_job(result, **over):
    fields = dict(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        prNumber=7,
        headSha="head-sha",
        headBranch="feat/x",
        baseBranch="main",
        authorUserId=AUTHOR_ID,
        prAuthorGithub="someone",
        deliveryId="d-1",
        installationId=42,
        prUrl="https://github.com/acme/api/pull/7",
        result=result.model_dump(mode="json"),
    )
    fields.update(over)
    return PipelineJob.model_construct(**fields)


def new_index(bb_memory_id="mem-new"):
    return MemoryIndex.model_construct(
        id=PydanticObjectId(),
        bbMemoryId=bb_memory_id,
        anchors=Anchors(repo="acme/api"),
    )


@pytest.fixture
def saves(monkeypatch):
    """Capture a deep copy of job.result at each save, and count saves."""
    snapshots: list[dict] = []

    async def fake_save(self, *a, **k):
        snapshots.append(copy.deepcopy(self.result))
        return self

    monkeypatch.setattr(PipelineJob, "save", fake_save)
    return snapshots


@pytest.fixture
def no_features(monkeypatch):
    """ensure_features has its own test; stub it out of the writer here."""
    mock = AsyncMock()
    monkeypatch.setattr(memory_write, "ensure_features", mock)
    return mock


@pytest.fixture
def bb():
    mock = AsyncMock()
    mock.add_memory.side_effect = [new_index(f"mem-{i}") for i in range(10)]
    return mock


async def run(bb, job):
    await write_distillation(bb=bb, job=job, org=make_org(), repo=make_repo())


# --- happy path + metadata ---------------------------------------------------


async def test_writes_decision_with_full_metadata(bb, saves, no_features):
    job = make_job(make_result([decision(files=["app/limits.py"], symbols=["RL"])]))

    await run(bb, job)

    _, kwargs = bb.add_memory.call_args
    assert kwargs["source"] == "claude_session"
    assert kwargs["commit_sha"] == "head-sha"
    assert kwargs["confidence"] == "verified"  # high → verified
    assert kwargs["feature"] == "rate-limiting"
    assert kwargs["pr_number"] == 7
    assert kwargs["author_user_id"] == AUTHOR_ID
    assert kwargs["repo"] == "acme/api"
    assert kwargs["files"] == ["app/limits.py"]
    assert kwargs["symbols"] == ["RL"]
    assert kwargs["supersedes"] == []
    assert kwargs["metadata"]["prUrl"] == job.prUrl
    assert kwargs["metadata"]["matchMode"] == "branch"
    assert kwargs["metadata"]["modelConfidence"] == "high"


@pytest.mark.parametrize(
    "model_conf,mapped",
    [("high", "verified"), ("medium", "unverified"), ("low", "unverified")],
)
async def test_confidence_mapping(bb, saves, no_features, model_conf, mapped):
    job = make_job(make_result([decision(confidence=model_conf)]))
    await run(bb, job)
    _, kwargs = bb.add_memory.call_args
    assert kwargs["confidence"] == mapped


async def test_conflicts_recorded_as_metadata(bb, saves, no_features, monkeypatch):
    # partial conflicts are recorded but never drive supersession
    conflicts = [
        ConsistencyConflict(
            bbMemoryId="mem-a", priorDecision="p", nature="n", severity="partial"
        ),
        ConsistencyConflict(
            bbMemoryId="mem-b", priorDecision="p", nature="n", severity="partial"
        ),
    ]
    job = make_job(make_result([decision()], conflicts=conflicts))
    await run(bb, job)

    _, kwargs = bb.add_memory.call_args
    assert kwargs["metadata"]["conflictsWith"] == ["mem-a", "mem-b"]
    assert kwargs["supersedes"] == []  # partials don't supersede


# --- checkpoint / resume -----------------------------------------------------


async def test_checkpoints_each_decision(bb, saves, no_features):
    job = make_job(make_result([decision(content="a"), decision(content="b")]))
    await run(bb, job)

    assert bb.add_memory.await_count == 2
    assert len(saves) == 2  # one save per decision
    # first checkpoint has decision 0 written, decision 1 still pending
    assert saves[0]["decisions"][0]["bbMemoryId"] == "mem-0"
    assert saves[0]["decisions"][1]["bbMemoryId"] is None
    # by the second, both are written
    assert saves[1]["decisions"][1]["bbMemoryId"] == "mem-1"
    assert job.result["decisions"][0]["bbMemoryId"] == "mem-0"


async def test_resume_skips_already_written_decisions(bb, saves, no_features):
    result = make_result([decision(content="a"), decision(content="b")])
    result.decisions[0].bbMemoryId = "mem-existing"  # already committed last run
    job = make_job(result)

    await run(bb, job)

    assert bb.add_memory.await_count == 1  # only the un-checkpointed decision
    assert job.result["decisions"][0]["bbMemoryId"] == "mem-existing"
    assert job.result["decisions"][1]["bbMemoryId"] == "mem-0"


# --- supersession ------------------------------------------------------------


def old_memory(bb_id="mem-old", files=("app/limits.py",), superseded_by=None):
    return MemoryIndex.model_construct(
        id=PydanticObjectId(),
        bbMemoryId=bb_id,
        contentSnapshot=f"[repo: acme/api] content of {bb_id}",
        anchors=Anchors(repo="acme/api", files=list(files)),
        supersededBy=superseded_by,
    )


async def test_direct_conflict_supersedes_max_overlap_decision(
    bb, saves, no_features, monkeypatch
):
    d0 = decision(content="unrelated", files=["app/other.py"])
    d1 = decision(content="replacement", files=["app/limits.py"])
    conflict = ConsistencyConflict(
        bbMemoryId="mem-old", priorDecision="p", nature="n", severity="direct"
    )
    old = old_memory(files=["app/limits.py"])  # overlaps d1
    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=old))
    job = make_job(make_result([d0, d1], conflicts=[conflict]))

    await run(bb, job)

    calls = {
        c.kwargs["content"]: c.kwargs["supersedes"]
        for c in bb.add_memory.call_args_list
    }
    assert calls["unrelated"] == []
    assert calls["replacement"] == [old.id]
    # note appended to the superseded memory, prefix stripped (no double [repo:])
    _, note_kwargs = bb.update_memory.call_args
    assert note_kwargs["memory_id"] == "mem-old"
    assert note_kwargs["content"].startswith("content of mem-old")
    assert "[superseded by PR #7 on " in note_kwargs["content"]
    assert not note_kwargs["content"].startswith("[repo:")


async def test_chain_followed_to_tail(bb, saves, no_features, monkeypatch):
    tail = old_memory(bb_id="mem-tail")
    head = old_memory(bb_id="mem-head", superseded_by=tail.id)
    conflict = ConsistencyConflict(
        bbMemoryId="mem-head", priorDecision="p", nature="n", severity="direct"
    )
    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=head))
    monkeypatch.setattr(MemoryIndex, "get", AsyncMock(return_value=tail))
    job = make_job(
        make_result([decision(files=["app/limits.py"])], conflicts=[conflict])
    )

    await run(bb, job)

    _, kwargs = bb.add_memory.call_args
    assert kwargs["supersedes"] == [tail.id]  # links to tail, not head


async def test_missing_target_skipped(bb, saves, no_features, monkeypatch):
    conflict = ConsistencyConflict(
        bbMemoryId="gone", priorDecision="p", nature="n", severity="direct"
    )
    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=None))
    job = make_job(make_result([decision()], conflicts=[conflict]))

    await run(bb, job)

    _, kwargs = bb.add_memory.call_args
    assert kwargs["supersedes"] == []
    bb.update_memory.assert_not_called()


async def test_note_failure_does_not_fail_job(bb, saves, no_features, monkeypatch):
    conflict = ConsistencyConflict(
        bbMemoryId="mem-old", priorDecision="p", nature="n", severity="direct"
    )
    monkeypatch.setattr(MemoryIndex, "find_one", AsyncMock(return_value=old_memory()))
    bb.update_memory.side_effect = RuntimeError("backboard down")
    job = make_job(
        make_result([decision(files=["app/limits.py"])], conflicts=[conflict])
    )

    await run(bb, job)  # must not raise

    assert job.result["decisions"][0]["bbMemoryId"] == "mem-0"  # write still committed


# --- ensure_features ---------------------------------------------------------


async def test_ensure_features_inserts_only_new(monkeypatch):
    from app.orgs import crud
    from app.orgs.models import Feature

    class FakeQuery:
        async def to_list(self):
            return [Feature.model_construct(orgId=ORG_ID, name="billing")]

    monkeypatch.setattr(
        Feature, "get_pymongo_collection", classmethod(lambda cls: None)
    )
    monkeypatch.setattr(Feature, "find", classmethod(lambda cls, *a, **k: FakeQuery()))
    inserted: list[Feature] = []

    async def fake_insert(self):
        inserted.append(self)
        return self

    monkeypatch.setattr(Feature, "insert", fake_insert)

    # "Rate Limiting" and "rate-limiting" both slugify to the same name
    await crud.ensure_features(
        ORG_ID, {"billing", "rate-limiting", "Rate Limiting"}, pr_number=7
    )

    assert [f.name for f in inserted] == ["rate-limiting"]
    assert inserted[0].description == "Coined by distillation from PR #7"


async def test_ensure_features_tolerates_duplicate(monkeypatch):
    from app.orgs import crud
    from app.orgs.models import Feature

    class FakeQuery:
        async def to_list(self):
            return []

    monkeypatch.setattr(
        Feature, "get_pymongo_collection", classmethod(lambda cls: None)
    )
    monkeypatch.setattr(Feature, "find", classmethod(lambda cls, *a, **k: FakeQuery()))

    async def boom(self):
        raise DuplicateKeyError("E11000")

    monkeypatch.setattr(Feature, "insert", boom)

    # a concurrent insert racing us must not raise
    await crud.ensure_features(ORG_ID, {"rate-limiting"}, pr_number=7)
