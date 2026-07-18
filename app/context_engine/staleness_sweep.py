"""Periodic staleness sweep: precompute verdicts so the graph reads a field.

`staleness_check` is live and correct but costs GitHub calls. Running it on every
graph render would be far too many; instead this sweep computes each active
memory's verdict on a schedule and caches it on ``memoryIndex.stalenessStatus`` /
``stalenessCheckedAt``. Graph rendering then reads those fields — zero GitHub
calls — while the retrieval path is free to recompute live for the handful of
memories it's about to use.

This is the write-side companion to the read-only `staleness_check`. It's the
single seam a real scheduler plugs into; nothing drives it automatically yet
(mirrors app/claude_hook's ``enqueue_normalization``). Per-memory failures are
logged and skipped so one bad memory never stalls the whole sweep, and the
underlying `RepoHistory` cache means files shared across memories are fetched
once per (base, head).
"""

import logging
from datetime import datetime

from beanie import PydanticObjectId

from app.backboard.models import MemoryIndex
from app.context_engine.schemas import StalenessVerdict
from app.context_engine.staleness import staleness_check
from app.github.client import GitHubApp
from app.github.history import RepoHistory, build_repo_history
from app.orgs.models import Org, Repo

logger = logging.getLogger(__name__)


async def _active_memories(repo_id: PydanticObjectId) -> list[MemoryIndex]:
    """Active (non-deleted) memories for a repo — the sweep's work set."""
    return await MemoryIndex.find({"repoId": repo_id, "deletedAt": None}).to_list()


async def refresh_staleness(
    memory: MemoryIndex,
    *,
    history: RepoHistory,
    ref: str | None = None,
) -> StalenessVerdict:
    """Compute one memory's verdict and persist it onto the memory.

    Stamps ``stalenessStatus`` and ``stalenessCheckedAt`` (the verdict's own check
    time) and saves. Returns the verdict for callers that also want it live."""
    verdict = await staleness_check(memory, history=history, ref=ref)
    memory.stalenessStatus = verdict.status
    memory.stalenessCheckedAt = datetime.fromisoformat(verdict.currentShaCheckedAt)
    await memory.save()
    return verdict


async def sweep_repo_staleness(
    *,
    org: Org,
    repo: Repo,
    gh: GitHubApp,
    ref: str | None = None,
) -> int:
    """Refresh cached staleness for every active memory in one repo.

    Returns the number of memories successfully refreshed. A repo with no GitHub
    installation (or a deactivated repo) can't be swept — that raises from
    `build_repo_history`, same as the live path. Individual memory failures are
    logged and skipped."""
    history = build_repo_history(org, repo, gh)
    refreshed = 0
    for memory in await _active_memories(repo.id):
        try:
            await refresh_staleness(memory, history=history, ref=ref)
            refreshed += 1
        except Exception:  # noqa: BLE001 — one bad memory must not stall the sweep
            logger.exception(
                "staleness refresh failed for memory %s", memory.bbMemoryId
            )
    logger.info(
        "swept staleness for %s/%s: %d memories", repo.owner, repo.name, refreshed
    )
    return refreshed
