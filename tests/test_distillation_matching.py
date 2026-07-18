from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.claude_hook.models import AgentSession
from app.distillation import matching
from app.job_queue.models import PipelineJob

REPO_ID = PydanticObjectId()
AUTHOR_ID = PydanticObjectId()


def make_job(**over) -> PipelineJob:
    fields = dict(
        orgId=PydanticObjectId(),
        repoId=REPO_ID,
        prNumber=7,
        headSha="abc123",
        headBranch="feat/x",
        baseBranch="main",
        authorUserId=AUTHOR_ID,
        prAuthorGithub="someone",
        deliveryId="d-1",
        installationId=42,
    )
    fields.update(over)
    return PipelineJob.model_construct(**fields)


def make_session(**over) -> AgentSession:
    fields = dict(
        id=PydanticObjectId(),
        orgId=PydanticObjectId(),
        repoId=REPO_ID,
        userId=AUTHOR_ID,
        sessionId=f"s-{PydanticObjectId()}",
        branch="feat/x",
        transcriptRef="0" * 24,
        status="normalized",
        createdAt=datetime.now(timezone.utc),
    )
    fields.update(over)
    return AgentSession.model_construct(**fields)


class FakeQuery:
    """Stands in for AgentSession.find(...): records the filter, supports the
    .sort(...).to_list() / .count() chain."""

    def __init__(self, docs):
        self.docs = docs
        self.sort_key = None

    def sort(self, key):
        self.sort_key = key
        return self

    async def to_list(self):
        return list(self.docs)

    async def count(self):
        return len(self.docs)


@pytest.fixture
def find_calls(monkeypatch):
    """Queue up per-call results for AgentSession.find; capture filters."""
    calls: list[dict] = []
    results: list[list[AgentSession]] = []

    def fake_find(cls, filter_dict):
        calls.append(filter_dict)
        docs = results.pop(0) if results else []
        return FakeQuery(docs)

    monkeypatch.setattr(AgentSession, "find", classmethod(fake_find))
    return calls, results


async def test_primary_branch_match(find_calls):
    calls, results = find_calls
    s1, s2 = make_session(), make_session()
    results.append([s1, s2])

    sessions, mode = await matching.match_sessions(make_job())

    assert (sessions, mode) == ([s1, s2], "branch")
    assert len(calls) == 1
    assert calls[0]["repoId"] == REPO_ID
    assert calls[0]["branch"] == "feat/x"
    assert calls[0]["status"] == "normalized"
    # re-runs may re-match sessions stamped by a prior attempt of THIS PR only
    assert calls[0]["prNumber"] == {"$in": [None, 7]}


async def test_fallback_only_when_primary_empty_and_author_linked(find_calls):
    calls, results = find_calls
    fallback_session = make_session(branch="feat/renamed")
    results.extend([[], [fallback_session]])

    before = datetime.now(timezone.utc)
    sessions, mode = await matching.match_sessions(make_job())

    assert (sessions, mode) == ([fallback_session], "author_recent")
    assert len(calls) == 2
    fallback = calls[1]
    assert fallback["userId"] == AUTHOR_ID
    assert "branch" not in fallback
    assert fallback["prNumber"] == {"$in": [None, 7]}
    cutoff = fallback["createdAt"]["$gte"]
    assert (before - cutoff) - matching.FALLBACK_WINDOW < timedelta(seconds=5)


async def test_no_fallback_without_linked_author(find_calls):
    calls, results = find_calls
    results.append([])

    sessions, mode = await matching.match_sessions(make_job(authorUserId=None))

    assert (sessions, mode) == ([], None)
    assert len(calls) == 1  # fallback query never issued


async def test_empty_fallback_is_a_coverage_gap(find_calls):
    calls, results = find_calls
    results.extend([[], []])

    sessions, mode = await matching.match_sessions(make_job())

    assert (sessions, mode) == ([], None)
    assert len(calls) == 2


async def test_stamp_matched_sets_pr_and_clears_ttl(monkeypatch):
    collection = AsyncMock()
    monkeypatch.setattr(
        AgentSession, "get_pymongo_collection", classmethod(lambda cls: collection)
    )
    s1, s2 = make_session(), make_session()

    await matching.stamp_matched([s1, s2], 7)

    (filter_dict, update), _ = collection.update_many.call_args
    assert filter_dict == {"_id": {"$in": [s1.id, s2.id]}}
    assert update["$set"]["prNumber"] == 7
    assert update["$set"]["expiresAt"] is None


async def test_stamp_matched_noop_on_empty(monkeypatch):
    collection = AsyncMock()
    monkeypatch.setattr(
        AgentSession, "get_pymongo_collection", classmethod(lambda cls: collection)
    )
    await matching.stamp_matched([], 7)
    collection.update_many.assert_not_called()


async def test_mark_distilled(monkeypatch):
    collection = AsyncMock()
    monkeypatch.setattr(
        AgentSession, "get_pymongo_collection", classmethod(lambda cls: collection)
    )
    s1 = make_session()

    await matching.mark_distilled([s1.id])  # now takes ids, not docs

    (filter_dict, update), _ = collection.update_many.call_args
    assert filter_dict == {"_id": {"$in": [s1.id]}}
    assert update["$set"]["status"] == "distilled"
