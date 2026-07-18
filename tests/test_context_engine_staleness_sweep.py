from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from beanie import PydanticObjectId

from app.context_engine import staleness_sweep
from app.context_engine.schemas import StalenessVerdict
from app.context_engine.staleness_sweep import refresh_staleness, sweep_repo_staleness

CHECKED_AT = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def make_verdict(status="stale"):
    return StalenessVerdict(
        status=status,
        memoryCommitSha="base",
        currentShaCheckedAt=CHECKED_AT.isoformat(),
        changedFiles=["app/a.py"],
        commitsSince=2,
        newerMemoryExists=True,
    )


def fake_memory():
    return SimpleNamespace(
        bbMemoryId="mem-1",
        stalenessStatus=None,
        stalenessCheckedAt=None,
        save=AsyncMock(),
    )


# ─── refresh_staleness (write path) ───────────────────────────────────────────


async def test_refresh_persists_status_and_checked_at(monkeypatch):
    monkeypatch.setattr(
        staleness_sweep, "staleness_check", AsyncMock(return_value=make_verdict("gap"))
    )
    memory = fake_memory()
    verdict = await refresh_staleness(memory, history=object())

    assert verdict.status == "gap"
    assert memory.stalenessStatus == "gap"
    assert memory.stalenessCheckedAt == CHECKED_AT  # parsed from the verdict
    memory.save.assert_awaited_once()


# ─── sweep_repo_staleness (orchestration) ─────────────────────────────────────


def _org_repo(monkeypatch):
    org = SimpleNamespace(id=PydanticObjectId(), githubInstallationId=1, slug="acme")
    repo = SimpleNamespace(
        id=PydanticObjectId(),
        owner="acme",
        name="api",
        defaultBranch="main",
        active=True,
    )
    # don't actually build a GitHub-backed history in the sweep test
    monkeypatch.setattr(
        staleness_sweep, "build_repo_history", lambda o, r, gh: SimpleNamespace()
    )
    return org, repo


async def test_sweep_refreshes_every_active_memory(monkeypatch):
    org, repo = _org_repo(monkeypatch)
    memories = [fake_memory(), fake_memory(), fake_memory()]
    monkeypatch.setattr(
        staleness_sweep, "_active_memories", AsyncMock(return_value=memories)
    )
    refresh = AsyncMock()
    monkeypatch.setattr(staleness_sweep, "refresh_staleness", refresh)

    count = await sweep_repo_staleness(org=org, repo=repo, gh=object())

    assert count == 3
    assert refresh.await_count == 3


async def test_sweep_skips_failures_and_counts_successes(monkeypatch):
    org, repo = _org_repo(monkeypatch)
    monkeypatch.setattr(
        staleness_sweep,
        "_active_memories",
        AsyncMock(return_value=[fake_memory(), fake_memory(), fake_memory()]),
    )
    # middle memory blows up; the sweep must keep going
    refresh = AsyncMock(side_effect=[None, RuntimeError("boom"), None])
    monkeypatch.setattr(staleness_sweep, "refresh_staleness", refresh)

    count = await sweep_repo_staleness(org=org, repo=repo, gh=object())

    assert count == 2
    assert refresh.await_count == 3
