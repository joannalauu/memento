from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from beanie import PydanticObjectId

from app.claude_hook.models import AgentSession
from app.claude_hook.normalizer import NormalizedEntry, render_jsonl
from app.context_engine.schemas import StalenessVerdict
from app.distillation import matching, pipeline
from app.distillation.schemas import DistillationOutput, DistilledDecision
from app.distillation.schemas import DecisionAnchors, StaleMemoryFlag
from app.github.client import GitHubError
from app.job_queue.models import PipelineJob
from app.orgs.models import Feature, Org, Repo

ORG_ID = PydanticObjectId()
REPO_ID = PydanticObjectId()

DIFF = """--- app/limits.py (modified, +10/-2)
@@ -1,4 +8,6 @@ def rate_limit
+def rate_limit(user):
"""


def make_org(**over):
    fields = dict(id=ORG_ID, name="Acme", slug="acme", bbAssistantId="asst-1")
    fields.update(over)
    return Org.model_construct(**fields)


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


def make_job(**over):
    fields = dict(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        prNumber=7,
        headSha="abc123",
        headBranch="feat/x",
        baseBranch="main",
        authorUserId=PydanticObjectId(),
        prAuthorGithub="someone",
        deliveryId="d-1",
        installationId=42,
        prTitle="Add rate limiting",
        status="running",
        attempts=1,
    )
    fields.update(over)
    return PipelineJob.model_construct(**fields)


def make_session(**over):
    fields = dict(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        userId=PydanticObjectId(),
        sessionId=f"s-{PydanticObjectId()}",
        branch="feat/x",
        transcriptRef="0" * 24,
        normalizedRef="1" * 24,
        normalizedTokenEstimate=100,
        status="normalized",
        createdAt=datetime.now(timezone.utc),
    )
    fields.update(over)
    return AgentSession.model_construct(**fields)


def normalized_blob():
    return render_jsonl(
        [NormalizedEntry(role="user", kind="text", text="please add rate limiting")]
    )


class FakeToolset:
    """Stands in for GitHubToolset: constructor signature must match."""

    diff = DIFF

    def __init__(self, gh, *, installation_id, owner, repo, default_branch):
        self.kwargs = dict(installation_id=installation_id, owner=owner, repo=repo)

    async def get_pr_diff(self, pr_number):
        return self.diff


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Happy-path world; individual tests break the piece they exercise."""
    saved_jobs: list[PipelineJob] = []

    async def fake_org_get(cls, _id):
        return make_org()

    async def fake_repo_get(cls, _id):
        return make_repo()

    async def fake_job_save(self, *a, **k):
        saved_jobs.append(self)
        return self

    monkeypatch.setattr(Org, "get", classmethod(fake_org_get))
    monkeypatch.setattr(Repo, "get", classmethod(fake_repo_get))
    monkeypatch.setattr(PipelineJob, "save", fake_job_save)
    monkeypatch.setattr(pipeline, "GitHubToolset", FakeToolset)
    FakeToolset.diff = DIFF

    sessions = [make_session()]

    async def fake_match(job):
        return list(sessions), "branch" if sessions else None

    async def fake_count_unnormalized(job):
        return 0

    stamped, distilled_sessions = [], []

    async def fake_stamp(s, pr):
        stamped.extend(s)

    async def fake_mark(s):
        distilled_sessions.extend(s)

    monkeypatch.setattr(matching, "match_sessions", fake_match)
    monkeypatch.setattr(matching, "count_unnormalized", fake_count_unnormalized)
    monkeypatch.setattr(matching, "stamp_matched", fake_stamp)
    monkeypatch.setattr(matching, "mark_distilled", fake_mark)

    async def fake_download(db, ref):
        return normalized_blob()

    monkeypatch.setattr(pipeline, "download_transcript_blob", fake_download)

    async def fake_related(anchors, **kwargs):
        return []

    monkeypatch.setattr(pipeline, "find_related_context", fake_related)

    class FakeFeatureQuery:
        async def to_list(self):
            return [Feature.model_construct(orgId=ORG_ID, name="billing")]

    monkeypatch.setattr(
        Feature, "find", classmethod(lambda cls, *a, **k: FakeFeatureQuery())
    )

    output = DistillationOutput(
        decisions=[
            DistilledDecision(
                content="Fixed-window limiter chosen for simplicity.",
                anchors=DecisionAnchors(files=["app/limits.py"]),
                feature="rate-limiting",
                confidence="high",
            )
        ]
    )
    distill_mock = AsyncMock(return_value=output)
    monkeypatch.setattr(pipeline, "distill", distill_mock)

    resp = Mock()  # .json() must be sync, like httpx.Response
    resp.json.return_value = {"body": "Adds a limiter."}
    gh = AsyncMock()
    gh.rest.return_value = resp

    # Staleness enrichment has its own tests; stub it out of the gap path here.
    staleness_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline, "_staleness_flags_for_gap", staleness_mock)

    return SimpleNamespaceLike(
        sessions=sessions,
        saved_jobs=saved_jobs,
        stamped=stamped,
        distilled_sessions=distilled_sessions,
        distill_mock=distill_mock,
        staleness_mock=staleness_mock,
        gh=gh,
        bb=AsyncMock(),
        db=object(),
    )


class SimpleNamespaceLike:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def run(env, job=None):
    await pipeline.run_pipeline_job(job or make_job(), db=env.db, bb=env.bb, gh=env.gh)


async def test_success_persists_result_and_flips_sessions(env):
    job = make_job()
    await run(env, job)

    assert job.status == "done"
    assert job.outcome == "distilled"
    assert job.finishedAt is not None
    assert env.saved_jobs == [job]
    assert job.result is not None
    assert job.result["matchMode"] == "branch"
    assert job.result["sessionIds"] == [str(env.sessions[0].id)]
    assert job.result["commitSha"] == job.headSha  # baseline for T3.3 staleness
    assert len(job.result["decisions"]) == 1
    assert env.stamped == env.sessions
    assert env.distilled_sessions == env.sessions

    # the distillation call saw the fetched PR body and the org's features
    _, kwargs = env.distill_mock.call_args
    assert kwargs["pr_description"] == "Adds a limiter."
    assert kwargs["feature_names"] == ["billing"]
    assert kwargs["assistant_id"] == "asst-1"


async def test_missing_org_is_terminal(env, monkeypatch):
    async def gone(cls, _id):
        return None

    monkeypatch.setattr(Org, "get", classmethod(gone))
    with pytest.raises(pipeline.TerminalJobError):
        await run(env)


async def test_inactive_repo_is_terminal(env, monkeypatch):
    async def inactive(cls, _id):
        return make_repo(active=False)

    monkeypatch.setattr(Repo, "get", classmethod(inactive))
    with pytest.raises(pipeline.TerminalJobError):
        await run(env)


async def test_zero_match_records_coverage_gap(env, monkeypatch):
    env.sessions.clear()

    async def three(job):
        return 3

    monkeypatch.setattr(matching, "count_unnormalized", three)
    job = make_job()
    await run(env, job)

    assert job.status == "done"
    assert job.outcome == "no_sessions"
    assert "3 un-normalized session(s)" in (job.gapDetail or "")
    assert env.saved_jobs == [job]
    env.distill_mock.assert_not_called()
    assert env.stamped == []
    # the gap runs staleness enrichment on the changed files
    env.staleness_mock.assert_awaited_once()


async def test_zero_match_records_staleness_flags(env, monkeypatch):
    env.sessions.clear()
    monkeypatch.setattr(matching, "count_unnormalized", AsyncMock(return_value=0))
    flag = StaleMemoryFlag(
        bbMemoryId="mem-1",
        verdict=StalenessVerdict(
            status="gap",
            memoryCommitSha="old-sha",
            currentShaCheckedAt="2026-07-18T00:00:00+00:00",
            changedFiles=["app/limits.py"],
        ),
    )
    env.staleness_mock.return_value = [flag]
    job = make_job()
    await run(env, job)

    assert job.outcome == "no_sessions"
    assert job.stalenessFlags == [flag.model_dump(mode="json")]
    assert "1 prior memory(ies) on changed files now stale/gap" in (job.gapDetail or "")


async def test_diff_error_string_is_transient(env):
    FakeToolset.diff = "Error: GitHub API error 502"
    with pytest.raises(pipeline.TransientJobError):
        await run(env)
    env.distill_mock.assert_not_called()


async def test_unparseable_distillation_is_transient(env):
    env.distill_mock.return_value = None
    job = make_job()
    with pytest.raises(pipeline.TransientJobError):
        await run(env, job)
    # sessions stay retryable: never flipped to distilled, job not saved as done
    assert env.distilled_sessions == []
    assert env.saved_jobs == []


async def test_unreadable_blobs_become_coverage_gap(env, monkeypatch):
    async def unreadable(db, ref):
        return None

    monkeypatch.setattr(pipeline, "download_transcript_blob", unreadable)
    job = make_job()
    await run(env, job)

    assert job.outcome == "no_sessions"
    assert "no normalized transcript" in (job.gapDetail or "")
    env.distill_mock.assert_not_called()


async def test_pr_body_fetch_failure_degrades_gracefully(env):
    env.gh.rest.side_effect = GitHubError("boom", status_code=500)
    job = make_job()
    await run(env, job)

    assert job.outcome == "distilled"
    _, kwargs = env.distill_mock.call_args
    assert kwargs["pr_description"] == ""
