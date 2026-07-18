"""Session↔PR matching: where the two capture timelines rendezvous.

The branch name is the join key — Epic 2 banked transcripts tagged with the
branch they were captured on, and the merged PR's head ref points back at it
via the `repo_branch_status` index. Squash merges need nothing special: we
match the stored branch tag, never a live GitHub branch lookup.

When the branch was renamed before the PR (zero primary matches), the fallback
casts a deliberately narrow net: only the PR author's own normalized sessions
in the repo within a recent window — pulling a teammate's unrelated session
into this PR's decision record is worse than a coverage gap.

Both queries admit `prNumber == job.prNumber` alongside `None` so a re-run of
a failed job can re-match the sessions it already stamped, while sessions
claimed by a *different* PR stay excluded.
"""

from datetime import datetime, timedelta, timezone

from app.claude_hook.models import AgentSession
from app.distillation.schemas import MatchMode
from app.job_queue.models import PipelineJob

FALLBACK_WINDOW = timedelta(days=7)


def _claimable_pr(pr_number: int) -> dict:
    return {"$in": [None, pr_number]}


async def match_sessions(
    job: PipelineJob,
) -> tuple[list[AgentSession], MatchMode | None]:
    """Return (sessions in capture order, how they matched) — ([], None) on a
    coverage gap. Time order matters: transcripts are concatenated as-is into
    the distillation prompt."""
    primary = (
        await AgentSession.find(
            {
                "repoId": job.repoId,
                "branch": job.headBranch,
                "status": "normalized",
                "prNumber": _claimable_pr(job.prNumber),
            }
        )
        .sort("+createdAt")
        .to_list()
    )
    if primary:
        return primary, "branch"

    if job.authorUserId is None:
        return [], None

    cutoff = datetime.now(timezone.utc) - FALLBACK_WINDOW
    fallback = (
        await AgentSession.find(
            {
                "repoId": job.repoId,
                "userId": job.authorUserId,
                "status": "normalized",
                "prNumber": _claimable_pr(job.prNumber),
                "createdAt": {"$gte": cutoff},
            }
        )
        .sort("+createdAt")
        .to_list()
    )
    if fallback:
        return fallback, "author_recent"
    return [], None


async def count_unnormalized(job: PipelineJob) -> int:
    """Sessions captured on the branch but not yet normalized — reported in
    the coverage-gap record so 'no sessions' is distinguishable from 'sessions
    stuck before the normalizer'."""
    return await AgentSession.find(
        {"repoId": job.repoId, "branch": job.headBranch, "status": "stored"}
    ).count()


async def stamp_matched(sessions: list[AgentSession], pr_number: int) -> None:
    """Attach matched sessions to the PR: set prNumber and clear expiresAt so
    the TTL can't reap them mid-pipeline (see AgentSession.expiresAt). Status
    stays "normalized" — it flips to "distilled" only after a successful
    distillation, so a failed job leaves the sessions retryable."""
    ids = [s.id for s in sessions if s.id is not None]
    if not ids:
        return
    await AgentSession.get_pymongo_collection().update_many(
        {"_id": {"$in": ids}},
        {
            "$set": {
                "prNumber": pr_number,
                "expiresAt": None,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )


async def mark_distilled(sessions: list[AgentSession]) -> None:
    """Flip successfully distilled sessions to their terminal status."""
    ids = [s.id for s in sessions if s.id is not None]
    if not ids:
        return
    await AgentSession.get_pymongo_collection().update_many(
        {"_id": {"$in": ids}},
        {
            "$set": {
                "status": "distilled",
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )
