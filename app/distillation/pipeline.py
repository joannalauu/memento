"""T3.2 orchestrator: one PipelineJob end to end.

The conversion point where captured-but-inert material becomes knowledge:
webhook's PR pointer → diff (T2.4) → anchors + related memories (T2.5) →
matched agentSessions → one distillation call → structured result persisted
on the job for T3.3 (memory write + supersession) to consume. Reasoning and
writing stay separate tickets because they retry differently — a re-run of
the write must not re-buy an expensive distillation call.

Failure vocabulary: raise TerminalJobError when a retry can't help (missing
org/repo, no installation); raise TransientJobError (or let transport errors
propagate) when it can. The zero-match coverage gap is neither — it's a
*successful* run whose outcome is "no_sessions", recorded on the job so the
admin health view can surface it. No engineer is ever contacted from here.
"""

import json
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from app.backboard.client import Backboard
from app.backboard.models import Anchors, MemoryIndex
from app.claude_hook.crud import download_transcript_blob
from app.claude_hook.models import AgentSession
from app.claude_hook.normalizer import NormalizedEntry
from app.context_engine import extract_anchors, find_related_context, staleness_check
from app.distillation import matching
from app.distillation.distill import distill
from app.distillation.schemas import DistillationResult, StaleMemoryFlag
from app.github.client import GitHubApp, GitHubError
from app.github.history import RepoHistory
from app.github.tools import GitHubToolset
from app.job_queue.models import PipelineJob
from app.orgs.models import Feature, Org, Repo

logger = logging.getLogger(__name__)

# Total budget for concatenated normalized transcripts (each is individually
# capped at ~30k by the normalizer). Newest sessions are kept first — they are
# closest to the merged state of the branch.
TRANSCRIPT_TOKEN_BUDGET = 60_000

# Cap on how many anchored memories a coverage gap runs staleness_check over —
# each is a handful of GitHub history reads, and the anchored set is normally
# small; this only guards a pathological hotspot file.
STALENESS_MEMORY_CAP = 25


class TerminalJobError(RuntimeError):
    """The job can never succeed as configured; do not requeue."""


class TransientJobError(RuntimeError):
    """The failure looks recoverable; the worker may requeue."""


def _render_transcript(session: AgentSession, blob: bytes, k: int, n: int) -> str:
    """Render one normalized JSONL blob as readable dialogue. Unparseable
    lines are kept raw — better noisy than silently missing."""
    lines = [f"### Session {k}/{n} (captured {session.createdAt:%Y-%m-%d %H:%M} UTC)"]
    for raw_line in blob.decode(errors="replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = NormalizedEntry.model_validate(json.loads(raw_line))
        except (json.JSONDecodeError, ValidationError, TypeError):
            lines.append(raw_line)
            continue
        lines.append(f"[{entry.role}] {entry.text}")
    return "\n".join(lines)


def _select_by_budget(
    sessions: list[AgentSession],
) -> tuple[list[AgentSession], list[AgentSession]]:
    """Keep the newest sessions whose estimates fit the budget (always at
    least the newest one); return (kept in capture order, dropped)."""
    kept: list[AgentSession] = []
    dropped: list[AgentSession] = []
    total = 0
    for session in reversed(sessions):  # newest first
        estimate = session.normalizedTokenEstimate or 0
        if kept and total + estimate > TRANSCRIPT_TOKEN_BUDGET:
            dropped.append(session)
            continue
        kept.append(session)
        total += estimate
    kept.reverse()
    dropped.reverse()
    return kept, dropped


async def _fetch_pr_description(gh: GitHubApp, repo: Repo, job: PipelineJob) -> str:
    """The job carries the PR title but not the body; fetch it. Best-effort —
    a missing description degrades the prompt, it doesn't fail the job."""
    try:
        resp = await gh.rest(
            "GET",
            f"/repos/{repo.owner}/{repo.name}/pulls/{job.prNumber}",
            installation_id=job.installationId,
        )
        return (resp.json() or {}).get("body") or ""
    except GitHubError as exc:
        logger.warning(
            "could not fetch PR body for %s/%s#%s: %s",
            repo.owner,
            repo.name,
            job.prNumber,
            exc,
        )
        return ""


async def _anchored_memories(repo_id, anchors: Anchors) -> list[MemoryIndex]:
    """Active memories whose anchors exactly overlap the diff's files/symbols —
    the ones a merge on these files could have made stale."""
    if not anchors.files and not anchors.symbols:
        return []
    return await MemoryIndex.find(
        {
            "repoId": repo_id,
            "deletedAt": None,
            "$or": [
                {"anchors.files": {"$in": anchors.files}},
                {"anchors.symbols": {"$in": anchors.symbols}},
            ],
        }
    ).to_list()


async def _staleness_flags_for_gap(
    job: PipelineJob, repo: Repo, *, gh: GitHubApp
) -> list[StaleMemoryFlag]:
    """Best-effort: a merge changed these files but no session was captured, so
    any prior memory about them may now be out of date. Run staleness_check on
    each and flag the non-fresh ones, so the coverage gap carries *what* went
    stale — never raises, staleness is enrichment, not the point of the record.
    """
    try:
        toolset = GitHubToolset(
            gh,
            installation_id=job.installationId,
            owner=repo.owner,
            repo=repo.name,
            default_branch=repo.defaultBranch,
        )
        diff = await toolset.get_pr_diff(job.prNumber)
        if diff.startswith("Error:"):
            logger.warning(
                "staleness skipped for gap pr=%s: %s", job.prNumber, diff[:200]
            )
            return []
        anchors = extract_anchors(diff, repo=f"{repo.owner}/{repo.name}")
        memories = await _anchored_memories(job.repoId, anchors)
        if not memories:
            return []
        history = RepoHistory(
            gh,
            installation_id=job.installationId,
            owner=repo.owner,
            repo=repo.name,
            default_branch=repo.defaultBranch,
        )
        flags: list[StaleMemoryFlag] = []
        # Staleness is judged against the branch the PR merged into.
        for memory in memories[:STALENESS_MEMORY_CAP]:
            verdict = await staleness_check(memory, history=history, ref=job.baseBranch)
            if verdict.status != "fresh":
                flags.append(
                    StaleMemoryFlag(bbMemoryId=memory.bbMemoryId, verdict=verdict)
                )
        return flags
    except Exception:  # noqa: BLE001 - enrichment must never fail the gap record
        logger.warning(
            "staleness enrichment failed for gap pr=%s", job.prNumber, exc_info=True
        )
        return []


async def _finish_no_sessions(
    job: PipelineJob,
    detail: str,
    staleness_flags: list[StaleMemoryFlag] | None = None,
) -> None:
    """A coverage gap is a completed run, not a failure: record nothing
    memory-wise, make the gap visible to the admin health view, stop."""
    logger.warning(
        "coverage gap: no sessions matched repo=%s pr=%s (%s)",
        job.repoId,
        job.prNumber,
        detail,
    )
    job.outcome = "no_sessions"
    job.gapDetail = detail
    job.stalenessFlags = (
        [f.model_dump(mode="json") for f in staleness_flags]
        if staleness_flags
        else None
    )
    job.status = "done"
    job.error = None
    job.finishedAt = datetime.now(timezone.utc)
    await job.save()


async def run_pipeline_job(
    job: PipelineJob, *, db: AsyncDatabase, bb: Backboard, gh: GitHubApp
) -> None:
    """Process one claimed job. On success (including the no_sessions gap)
    the job doc is updated to done here; failures raise for the worker's
    retry/terminal bookkeeping."""
    org = await Org.get(job.orgId)
    repo = await Repo.get(job.repoId)
    if org is None or repo is None:
        raise TerminalJobError(f"org {job.orgId} or repo {job.repoId} no longer exists")
    if not org.bbAssistantId:
        raise TerminalJobError(f"org {org.id} has no Backboard assistant")
    if not repo.active:
        raise TerminalJobError(f"repo {repo.id} is inactive")

    # ── match: the branch name joins the two timelines ──────────────────────
    sessions, match_mode = await matching.match_sessions(job)
    if not sessions or match_mode is None:
        unnormalized = await matching.count_unnormalized(job)
        detail = f"branch={job.headBranch!r}"
        if job.authorUserId is None:
            detail += ", author-fallback unavailable (no linked user)"
        else:
            detail += ", author-fallback found nothing"
        if unnormalized:
            detail += f", {unnormalized} un-normalized session(s) on branch"
        # The merge changed code with nothing captured — flag prior memories on
        # those files that have now gone stale, so the gap isn't silent.
        flags = await _staleness_flags_for_gap(job, repo, gh=gh)
        if flags:
            detail += f", {len(flags)} prior memory(ies) on changed files now stale/gap"
        await _finish_no_sessions(job, detail, flags)
        return
    await matching.stamp_matched(sessions, job.prNumber)

    # ── gather: diff, anchors, prior context, transcripts, features ─────────
    toolset = GitHubToolset(
        gh,
        installation_id=job.installationId,
        owner=repo.owner,
        repo=repo.name,
        default_branch=repo.defaultBranch,
    )
    diff = await toolset.get_pr_diff(job.prNumber)
    if diff.startswith("Error:"):
        raise TransientJobError(f"get_pr_diff failed: {diff[:300]}")

    repo_full = f"{repo.owner}/{repo.name}"
    anchors = extract_anchors(diff, repo=repo_full)
    related = await find_related_context(
        anchors, bb=bb, assistant_id=org.bbAssistantId, repo_id=job.repoId
    )
    pr_description = await _fetch_pr_description(gh, repo, job)

    kept, dropped = _select_by_budget(sessions)
    downloaded: list[tuple[AgentSession, bytes]] = []
    for session in kept:
        blob = (
            await download_transcript_blob(db, session.normalizedRef)
            if session.normalizedRef
            else None
        )
        if blob is None:
            logger.warning(
                "normalized blob unreadable for agentSession %s; skipping", session.id
            )
            continue
        downloaded.append((session, blob))
    readable = [session for session, _ in downloaded]
    blocks = [
        _render_transcript(session, blob, k, len(downloaded))
        for k, (session, blob) in enumerate(downloaded, start=1)
    ]
    if not readable:
        await _finish_no_sessions(
            job,
            f"matched {len(sessions)} session(s) but no normalized transcript "
            "was readable",
        )
        return

    feature_names = sorted(
        {f.name for f in await Feature.find({"orgId": job.orgId}).to_list()}
    )

    # ── distill: the one controlled memory="off" call ────────────────────────
    output = await distill(
        bb=bb,
        assistant_id=org.bbAssistantId,
        pr_number=job.prNumber,
        pr_title=job.prTitle or "",
        branch=job.headBranch,
        pr_description=pr_description,
        anchors=anchors,
        feature_names=feature_names,
        related=related,
        transcript_block="\n\n".join(blocks),
        dropped_sessions=len(dropped),
    )
    if output is None:
        raise TransientJobError("distillation response was not a JSON object")

    # ── persist for T3.3, then flip sessions to their terminal status ───────
    now = datetime.now(timezone.utc)
    result = DistillationResult(
        decisions=output.decisions,
        conflicts=output.conflicts,
        sessionIds=[s.id for s in readable if s.id is not None],
        droppedSessionIds=[s.id for s in dropped if s.id is not None],
        matchMode=match_mode,
        commitSha=job.headSha,  # T3.3 stamps this → future staleness baseline
        distilledAt=now,
    )
    job.result = result.model_dump(mode="json")
    job.outcome = "distilled"
    job.status = "done"
    job.error = None
    job.finishedAt = now
    await job.save()
    await matching.mark_distilled(sessions)
    logger.info(
        "distilled pr=%s repo=%s: %d decision(s), %d conflict(s) from %d session(s)",
        job.prNumber,
        job.repoId,
        len(output.decisions),
        len(output.conflicts),
        len(readable),
    )
