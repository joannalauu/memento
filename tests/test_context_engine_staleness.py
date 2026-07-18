from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine import staleness
from app.context_engine.staleness import staleness_check
from app.github.client import GitHubError

REPO = "acme/api-server"
BASE_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_memory(sha="base-sha", files=("app/a.py",), symbols=(), created=BASE_DATE):
    return MemoryIndex.model_construct(
        id=PydanticObjectId(),
        bbMemoryId="mem-1",
        repoId=PydanticObjectId(),
        commitSha=sha,
        createdAt=created,
        contentSnapshot="x",
        anchors=Anchors(repo=REPO, files=list(files), symbols=list(symbols)),
    )


class FakeHistory:
    """Stand-in RepoHistory. ``per_path`` maps a path to its commit shas (or an
    exception to raise); ``base_date`` is returned by commit_date (or raised)."""

    def __init__(self, base_date=BASE_DATE, per_path=None):
        self._base_date = base_date
        self._per_path = per_path or {}
        self.path_calls: list[tuple] = []
        self.date_calls: list[str] = []

    async def commit_date(self, sha):
        self.date_calls.append(sha)
        if isinstance(self._base_date, Exception):
            raise self._base_date
        return self._base_date

    async def commits_touching_path_since(self, path, *, since, ref=None):
        self.path_calls.append((path, since, ref))
        value = self._per_path.get(path, [])
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def no_newer(monkeypatch):
    monkeypatch.setattr(
        staleness, "_newer_memory_exists", AsyncMock(return_value=False)
    )


@pytest.fixture
def has_newer(monkeypatch):
    monkeypatch.setattr(staleness, "_newer_memory_exists", AsyncMock(return_value=True))


# ─── three-way verdict ────────────────────────────────────────────────────────


async def test_fresh_when_no_anchored_file_moved(no_newer):
    history = FakeHistory(per_path={"app/a.py": []})
    v = await staleness_check(make_memory(files=["app/a.py"]), history=history)
    assert v.status == "fresh"
    assert v.changedFiles == []
    assert v.commitsSince == 0
    assert v.newerMemoryExists is False
    assert v.memoryCommitSha == "base-sha"
    # the base commit date bounded the per-path query
    assert history.path_calls == [("app/a.py", BASE_DATE, None)]


async def test_gap_when_file_moved_and_nothing_newer(no_newer):
    history = FakeHistory(per_path={"app/a.py": ["c1", "c2"]})
    v = await staleness_check(make_memory(files=["app/a.py"]), history=history)
    assert v.status == "gap"
    assert v.changedFiles == ["app/a.py"]
    assert v.commitsSince == 2


async def test_stale_when_file_moved_but_newer_memory_covers(has_newer):
    history = FakeHistory(per_path={"app/a.py": ["c1"]})
    v = await staleness_check(make_memory(files=["app/a.py"]), history=history)
    assert v.status == "stale"
    assert v.changedFiles == ["app/a.py"]
    assert v.newerMemoryExists is True


async def test_commits_since_counts_distinct_across_files(no_newer):
    # c2 touches both files; it must be counted once.
    history = FakeHistory(per_path={"app/a.py": ["c1", "c2"], "app/b.py": ["c2", "c3"]})
    v = await staleness_check(
        make_memory(files=["app/a.py", "app/b.py"]), history=history
    )
    assert v.status == "gap"
    assert sorted(v.changedFiles) == ["app/a.py", "app/b.py"]
    assert v.commitsSince == 3


# ─── undeterminable cases never claim fresh ───────────────────────────────────


async def test_missing_commit_sha_is_gap_without_touching_history(no_newer):
    history = FakeHistory()
    v = await staleness_check(make_memory(sha=None), history=history)
    assert v.status == "gap"
    assert v.memoryCommitSha == ""
    assert v.commitsSince is None
    assert history.date_calls == [] and history.path_calls == []


async def test_missing_commit_sha_with_newer_is_stale(has_newer):
    v = await staleness_check(make_memory(sha=""), history=FakeHistory())
    assert v.status == "stale"


async def test_no_file_anchors_is_gap(no_newer):
    v = await staleness_check(
        make_memory(files=[], symbols=["Foo"]), history=FakeHistory()
    )
    assert v.status == "gap"
    assert v.commitsSince is None


async def test_base_commit_gone_is_undeterminable(no_newer):
    history = FakeHistory(base_date=None)  # commit_date -> None (404/GC'd)
    v = await staleness_check(make_memory(), history=history)
    assert v.status == "gap"
    assert v.commitsSince is None
    assert history.path_calls == []  # never probed paths without a baseline


async def test_partial_history_failure_does_not_claim_fresh(no_newer):
    # one path reads clean, the other errors — can't confirm fresh.
    history = FakeHistory(per_path={"app/a.py": [], "app/b.py": GitHubError("boom")})
    v = await staleness_check(
        make_memory(files=["app/a.py", "app/b.py"]), history=history
    )
    assert v.status == "gap"
    assert v.changedFiles == []
    assert v.commitsSince is None


async def test_commit_date_error_propagating_is_undeterminable(no_newer):
    history = FakeHistory(base_date=GitHubError("500"))
    v = await staleness_check(make_memory(), history=history)
    assert v.status == "gap"
    assert v.commitsSince is None


# ─── knobs ────────────────────────────────────────────────────────────────────


async def test_anchors_override_and_ref_are_used(no_newer):
    history = FakeHistory(per_path={"override/x.py": []})
    override = Anchors(repo=REPO, files=["override/x.py"], symbols=[])
    v = await staleness_check(
        make_memory(files=["app/a.py"]),
        history=history,
        anchors=override,
        ref="release",
    )
    assert v.status == "fresh"
    assert history.path_calls == [("override/x.py", BASE_DATE, "release")]


async def test_checked_at_is_iso_timestamp(no_newer):
    v = await staleness_check(make_memory(), history=FakeHistory())
    # parseable back to a datetime
    assert datetime.fromisoformat(v.currentShaCheckedAt).tzinfo is not None


# ─── newer-memory query ───────────────────────────────────────────────────────


async def test_newer_memory_query_shape(monkeypatch):
    captured = {}

    async def fake_find_one(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(MemoryIndex, "find_one", staticmethod(fake_find_one))
    memory = make_memory(files=["app/a.py"], symbols=["Foo"])
    result = await staleness._newer_memory_exists(memory, ["app/a.py"], ["Foo"])
    assert result is False
    q = captured["query"]
    assert q["repoId"] == memory.repoId
    assert q["deletedAt"] is None
    assert q["_id"] == {"$ne": memory.id}
    assert q["createdAt"] == {"$gt": memory.createdAt}
    assert q["$or"] == [
        {"anchors.files": {"$in": ["app/a.py"]}},
        {"anchors.symbols": {"$in": ["Foo"]}},
    ]


async def test_newer_memory_short_circuits_on_empty_anchors(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(MemoryIndex, "find_one", staticmethod(called))
    assert await staleness._newer_memory_exists(make_memory(), [], []) is False
    called.assert_not_awaited()
