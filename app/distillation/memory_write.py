"""T3.3 — commit distilled decisions to both stores, with supersession.

The write half of the pipeline. T3.2 produced a `DistillationResult` (persisted
on `PipelineJob.result`); this turns each decision into a memory in Backboard
(the anchor) mirrored into `memoryIndex` (the graph node), registers any newly
coined feature labels, and links prior decisions the change replaces.

Ordering discipline (there is no cross-system transaction): Backboard write
first, then a per-decision checkpoint back onto `job.result` so a crash resumes
without re-writing an already-committed decision. `bb.add_memory` owns the
index mirror, the `supersededBy` edge, and the staleness flips; this module
decides *what* supersedes what and appends the human-readable note.

Supersession vs conflict — the load-bearing distinction, carried from T3.2's
`ConsistencyConflict.severity`:
- ``direct`` (the change reverses a prior decision) → intentional replacement:
  link old→new, mark old superseded, append a dated note. Both coexist with a
  clear temporal order; nothing is deleted.
- ``partial`` (the change erodes a prior decision) → recorded only, on the new
  memory's ``conflictsWith`` metadata. An honest flag, not a silent overwrite.
"""

import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.backboard.client import Backboard
from app.backboard.models import MemoryConfidence, MemoryIndex
from app.context_engine.schemas import ConsistencyConflict
from app.distillation.schemas import (
    DecisionConfidence,
    DistillationResult,
    DistilledDecision,
)
from app.job_queue.models import PipelineJob
from app.orgs.crud import ensure_features
from app.orgs.models import Org, Repo

logger = logging.getLogger(__name__)


def _map_confidence(confidence: DecisionConfidence) -> MemoryConfidence:
    """A decision the transcript stated explicitly (``high``) is as verified as
    a distilled `claude_session` memory gets; anything softer is unverified.
    The raw model label is preserved in metadata for auditing."""
    return "verified" if confidence == "high" else "unverified"


def _strip_repo_prefix(content: str, repo: str) -> str:
    """Drop the ``[repo: X] `` prefix so appending a note and handing it back to
    ``update_memory`` (which re-injects the prefix) doesn't double it."""
    prefix = f"[repo: {repo}] "
    return content[len(prefix) :] if content.startswith(prefix) else content


def _overlap(decision: DistilledDecision, memory: MemoryIndex) -> int:
    """How many anchors a decision shares with an existing memory — the signal
    for which new decision most naturally replaces an old one."""
    files = set(decision.anchors.files) & set(memory.anchors.files)
    symbols = set(decision.anchors.symbols) & set(memory.anchors.symbols)
    return len(files) + len(symbols)


def _best_overlapping_decision(
    decisions: list[DistilledDecision], target: MemoryIndex
) -> int:
    """Index of the decision that most overlaps ``target``'s anchors — the
    supersession edge should point at the new node closest to the old one.
    Ties (including no overlap at all) fall to the first decision."""
    return max(range(len(decisions)), key=lambda i: _overlap(decisions[i], target))


async def _resolve_target(
    conflict: ConsistencyConflict, repo_id: PydanticObjectId, own_bb_ids: set[str]
) -> MemoryIndex | None:
    """Find the live tail of the chain a direct conflict points at.

    Follows ``supersededBy`` so a new decision links to the *most recent* prior
    decision (PR #300 → #217, not #142). Returns None — skip — when the cited
    memory is gone or the tail is one of this job's own new memories (already
    handled on a resume)."""
    old = await MemoryIndex.find_one(
        {"bbMemoryId": conflict.bbMemoryId, "repoId": repo_id, "deletedAt": None}
    )
    if old is None:
        logger.info("supersession target %s not found; skipping", conflict.bbMemoryId)
        return None
    seen: set[PydanticObjectId] = set()
    while old.supersededBy is not None and old.id not in seen:
        assert old.id is not None, "MemoryIndex must have an id"
        seen.add(old.id)
        nxt = await MemoryIndex.get(old.supersededBy)
        if nxt is None:
            break
        old = nxt
    if old.bbMemoryId in own_bb_ids:
        return None  # already superseded by this job's own write
    return old


async def write_distillation(
    job: PipelineJob, *, bb: Backboard, org: Org, repo: Repo
) -> None:
    """Write every distilled decision for a job to both stores, apply direct-
    conflict supersession, and register new features. Idempotent on resume:
    decisions already carrying a ``bbMemoryId`` are skipped.

    Raises on a Backboard/Mongo write failure so the worker requeues; the
    checkpointed decisions make the retry pick up where it stopped. Supersession
    notes are best-effort and never fail the job.
    """
    assert job.result is not None, "write_distillation requires a distilled result"
    result = DistillationResult.model_validate(job.result)
    decisions = result.decisions
    assistant_id = org.bbAssistantId
    repo_full = f"{repo.owner}/{repo.name}"
    conflict_ids = [c.bbMemoryId for c in result.conflicts]

    # Resolve direct-conflict supersession targets and assign each to the new
    # decision it most overlaps — the edge should point at the closest new node.
    own_bb_ids = {d.bbMemoryId for d in decisions if d.bbMemoryId}
    targets_for: dict[int, list[MemoryIndex]] = {}
    if decisions:
        for conflict in result.conflicts:
            if conflict.severity != "direct":
                continue
            target = await _resolve_target(conflict, job.repoId, own_bb_ids)
            if target is None:
                continue
            best = _best_overlapping_decision(decisions, target)
            targets_for.setdefault(best, []).append(target)

    base_metadata: dict = {
        "prUrl": job.prUrl,
        "matchMode": result.matchMode,
    }
    if conflict_ids:
        base_metadata["conflictsWith"] = conflict_ids

    for i, decision in enumerate(decisions):
        if decision.bbMemoryId:  # already committed on a prior attempt
            continue
        targets = targets_for.get(i, [])
        index = await bb.add_memory(
            assistant_id=assistant_id,
            org_id=job.orgId,
            repo_id=job.repoId,
            repo=repo_full,
            content=decision.content,
            source="claude_session",
            confidence=_map_confidence(decision.confidence),
            feature=decision.feature,
            pr_number=job.prNumber,
            commit_sha=result.commitSha,
            author_user_id=job.authorUserId,
            files=decision.anchors.files,
            symbols=decision.anchors.symbols,
            supersedes=[t.id for t in targets if t.id is not None],
            metadata={**base_metadata, "modelConfidence": decision.confidence},
        )
        # Checkpoint immediately: Backboard is committed, so record the id before
        # anything else can fail — a resume then skips this decision.
        decision.bbMemoryId = index.bbMemoryId
        job.result = result.model_dump(mode="json")
        await job.save()

        # Best-effort human-readable note on each superseded memory. The graph
        # edge (supersededBy) is already durable via add_memory; the note is
        # Backboard-content garnish, so a failure here must not fail the job.
        for target in targets:
            await _append_supersession_note(
                bb, assistant_id, target, repo_full, job.prNumber
            )

    await ensure_features(
        job.orgId, {d.feature for d in decisions}, pr_number=job.prNumber
    )


async def _append_supersession_note(
    bb: Backboard,
    assistant_id: str,
    target: MemoryIndex,
    repo_full: str,
    pr_number: int,
) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = f"\n\n[superseded by PR #{pr_number} on {today}]"
    try:
        await bb.update_memory(
            assistant_id=assistant_id,
            memory_id=target.bbMemoryId,
            repo=repo_full,
            content=_strip_repo_prefix(target.contentSnapshot, repo_full) + note,
            metadata={"superseded": True, "supersededByPr": pr_number},
        )
    except Exception:  # noqa: BLE001 - note is garnish; the edge is already durable
        logger.warning(
            "could not append supersession note to %s", target.bbMemoryId, exc_info=True
        )
