import hashlib
import hmac
import json

import pytest
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError

from app.claude_hook import crud as hook_crud
from app.claude_hook.models import WebhookEvent
from app.github import routes as github_routes
from app.github.client import GitHubApp, GitHubSettings, get_github
from app.job_queue import crud as job_crud
from app.job_queue.models import PipelineJob
from app.main import app

WEBHOOK_SECRET = "test-webhook-secret"


def _sign(body: bytes) -> str:
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _merged_pr_payload(**over) -> dict:
    payload = {
        "action": "closed",
        "installation": {"id": 42},
        "repository": {"id": 123},
        "pull_request": {
            "number": 7,
            "merged": True,
            "title": "Add thing",
            "html_url": "https://github.com/acme/api/pull/7",
            "merged_at": "2026-07-18T00:00:00Z",
            "user": {"login": "SomeOne"},
            "head": {"ref": "feat/x", "sha": "abc123"},
            "base": {"ref": "main"},
        },
    }
    payload.update(over)
    return payload


# --- claim_webhook_event / finish_webhook_event -----------------------------


@pytest.fixture(autouse=True)
def stub_beanie(monkeypatch):
    # Document.__init__ calls get_pymongo_collection() only as an "init_beanie
    # has run" guard; stub it so constructing docs needs no Mongo.
    for model in (WebhookEvent, PipelineJob):
        monkeypatch.setattr(
            model, "get_pymongo_collection", classmethod(lambda cls: None)
        )


async def test_claim_inserts_fresh(monkeypatch):
    inserted = []

    async def fake_insert(self):
        inserted.append(self)
        return self

    monkeypatch.setattr(WebhookEvent, "insert", fake_insert)

    doc = await hook_crud.claim_webhook_event("d-1", "pull_request", {"a": 1})

    assert inserted == [doc]
    assert doc.status == "received"
    assert doc.deliveryId == "d-1"


@pytest.mark.parametrize("terminal_status", ["processed", "skipped"])
async def test_claim_returns_none_on_terminal_duplicate(monkeypatch, terminal_status):
    existing = WebhookEvent.model_construct(deliveryId="d-1", status=terminal_status)

    async def fake_insert(self):
        raise DuplicateKeyError("E11000 duplicate key")

    async def fake_find_one(*a, **k):
        return existing

    monkeypatch.setattr(WebhookEvent, "insert", fake_insert)
    monkeypatch.setattr(WebhookEvent, "find_one", staticmethod(fake_find_one))

    assert await hook_crud.claim_webhook_event("d-1", "pull_request", {}) is None


@pytest.mark.parametrize("reclaimable_status", ["received", "failed"])
async def test_claim_hands_back_reclaimable_duplicate(monkeypatch, reclaimable_status):
    existing = WebhookEvent.model_construct(deliveryId="d-1", status=reclaimable_status)

    async def fake_insert(self):
        raise DuplicateKeyError("E11000 duplicate key")

    async def fake_find_one(*a, **k):
        return existing

    monkeypatch.setattr(WebhookEvent, "insert", fake_insert)
    monkeypatch.setattr(WebhookEvent, "find_one", staticmethod(fake_find_one))

    assert await hook_crud.claim_webhook_event("d-1", "pull_request", {}) is existing


async def test_finish_sets_status_and_timestamp(monkeypatch):
    saved = []

    async def fake_save(self, *a, **k):
        saved.append(self)
        return self

    monkeypatch.setattr(WebhookEvent, "save", fake_save)
    event = WebhookEvent.model_construct(
        deliveryId="d-1", status="received", processedAt=None
    )

    await hook_crud.finish_webhook_event(event, "processed")

    assert saved == [event]
    assert event.status == "processed"
    assert event.processedAt is not None


# --- enqueue_pipeline_job ---------------------------------------------------


def _job_kwargs(**over):
    base = dict(
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        pr_number=7,
        head_sha="abc123",
        head_branch="feat/x",
        base_branch="main",
        author_user_id=None,
        pr_author_github="someone",
        delivery_id="d-1",
        installation_id=42,
    )
    base.update(over)
    return base


async def test_enqueue_inserts_queued_job(monkeypatch):
    inserted = []

    async def fake_insert(self):
        inserted.append(self)
        return self

    monkeypatch.setattr(PipelineJob, "insert", fake_insert)

    job = await job_crud.enqueue_pipeline_job(**_job_kwargs())

    assert inserted == [job]
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.headBranch == "feat/x"


async def test_enqueue_treats_duplicate_as_already_enqueued(monkeypatch):
    async def fake_insert(self):
        raise DuplicateKeyError("E11000 duplicate key")

    monkeypatch.setattr(PipelineJob, "insert", fake_insert)

    assert await job_crud.enqueue_pipeline_job(**_job_kwargs()) is None


# --- POST /github/webhook ---------------------------------------------------


class _Recorder:
    """In-memory stand-ins for the route's crud calls."""

    def __init__(self):
        self.claims: list[tuple[str, str]] = []
        self.finishes: list[str] = []
        self.enqueues: list[dict] = []
        self.claim_result = "fresh"  # "fresh" | None
        self.org = None
        self.repo = None
        self.user = None

    def install(self, monkeypatch):
        async def claim(delivery_id, event_type, payload):
            self.claims.append((delivery_id, event_type))
            if self.claim_result is None:
                return None
            return WebhookEvent.model_construct(
                deliveryId=delivery_id, eventType=event_type, status="received"
            )

        async def finish(event, status):
            self.finishes.append(status)

        async def enqueue(**kwargs):
            self.enqueues.append(kwargs)
            return None

        async def get_org(installation_id):
            return self.org

        async def get_repo(org_id, github_repo_id):
            return self.repo

        async def find_user(*a, **k):
            return self.user

        monkeypatch.setattr(github_routes, "claim_webhook_event", claim)
        monkeypatch.setattr(github_routes, "finish_webhook_event", finish)
        monkeypatch.setattr(github_routes, "enqueue_pipeline_job", enqueue)
        monkeypatch.setattr(github_routes, "get_org_by_installation", get_org)
        monkeypatch.setattr(github_routes, "get_repo_by_github_id", get_repo)
        monkeypatch.setattr(github_routes.User, "find_one", staticmethod(find_user))


def _org():
    class _Org:
        id = PydanticObjectId()

    return _Org()


def _repo(active=True):
    class _Repo:
        id = PydanticObjectId()

    r = _Repo()
    r.active = active
    return r


@pytest.fixture
def webhook_client(monkeypatch):
    github = GitHubApp(GitHubSettings(app_id="test-app", webhook_secret=WEBHOOK_SECRET))
    app.dependency_overrides[get_github] = lambda: github
    recorder = _Recorder()
    recorder.install(monkeypatch)

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client, recorder
    finally:
        app.dependency_overrides.clear()


async def _post(
    client, payload: dict, *, event="pull_request", delivery="d-1", sig=None
):
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": sig if sig is not None else _sign(body),
    }
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    async with client:
        return await client.post("/github/webhook", content=body, headers=headers)


async def test_rejects_bad_signature(webhook_client):
    client, rec = webhook_client
    resp = await _post(client, _merged_pr_payload(), sig="sha256=deadbeef")
    assert resp.status_code == 401
    assert rec.claims == []  # unauthenticated bodies never touch the DB


async def test_skips_missing_delivery_id(webhook_client):
    client, rec = webhook_client
    resp = await _post(client, _merged_pr_payload(), delivery=None)
    assert resp.status_code == 200
    assert resp.json()["skipped"] is True
    assert rec.claims == []


async def test_duplicate_delivery_short_circuits(webhook_client):
    client, rec = webhook_client
    rec.claim_result = None
    resp = await _post(client, _merged_pr_payload())
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is True
    assert rec.finishes == []
    assert rec.enqueues == []


async def test_irrelevant_event_marked_skipped(webhook_client):
    client, rec = webhook_client
    resp = await _post(client, {"zen": "Design for failure."}, event="ping")
    assert resp.status_code == 200
    assert rec.finishes == ["skipped"]
    assert rec.enqueues == []


async def test_closed_unmerged_pr_skipped(webhook_client):
    client, rec = webhook_client
    payload = _merged_pr_payload()
    payload["pull_request"]["merged"] = False
    resp = await _post(client, payload)
    assert resp.status_code == 200
    assert rec.finishes == ["skipped"]
    assert rec.enqueues == []


async def test_unknown_installation_skipped(webhook_client):
    client, rec = webhook_client
    rec.org = None
    resp = await _post(client, _merged_pr_payload())
    assert resp.status_code == 200
    assert rec.finishes == ["skipped"]
    assert rec.enqueues == []


async def test_inactive_repo_skipped(webhook_client):
    client, rec = webhook_client
    rec.org = _org()
    rec.repo = _repo(active=False)
    resp = await _post(client, _merged_pr_payload())
    assert resp.status_code == 200
    assert rec.finishes == ["skipped"]
    assert rec.enqueues == []


async def test_merged_pr_enqueues_job(webhook_client):
    client, rec = webhook_client
    rec.org = _org()
    rec.repo = _repo()
    resp = await _post(client, _merged_pr_payload())

    assert resp.status_code == 200
    assert rec.claims == [("d-1", "pull_request")]
    assert rec.finishes == ["processed"]
    assert len(rec.enqueues) == 1
    job = rec.enqueues[0]
    assert job["org_id"] == rec.org.id
    assert job["repo_id"] == rec.repo.id
    assert job["pr_number"] == 7
    assert job["head_sha"] == "abc123"
    assert job["head_branch"] == "feat/x"
    assert job["base_branch"] == "main"
    assert job["author_user_id"] is None  # login unlinked → non-fatal
    assert job["pr_author_github"] == "someone"  # lowercased
    assert job["delivery_id"] == "d-1"
    assert job["installation_id"] == 42
    assert job["merged_at"] is not None


async def test_merged_pr_links_known_author(webhook_client):
    client, rec = webhook_client
    rec.org = _org()
    rec.repo = _repo()

    class _User:
        id = PydanticObjectId()

    rec.user = _User()
    resp = await _post(client, _merged_pr_payload())
    assert resp.status_code == 200
    assert rec.enqueues[0]["author_user_id"] == rec.user.id


async def test_installation_deleted_still_handled(webhook_client, monkeypatch):
    client, rec = webhook_client
    cleared = []

    async def fake_clear(installation_id):
        cleared.append(installation_id)

    monkeypatch.setattr(github_routes, "clear_github_installation", fake_clear)
    resp = await _post(
        client,
        {"action": "deleted", "installation": {"id": 42}},
        event="installation",
    )
    assert resp.status_code == 200
    assert cleared == [42]
    assert rec.finishes == ["processed"]


async def test_processing_failure_returns_500_and_marks_failed(
    webhook_client, monkeypatch
):
    client, rec = webhook_client

    async def boom(installation_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(github_routes, "get_org_by_installation", boom)
    resp = await _post(client, _merged_pr_payload())
    assert resp.status_code == 500  # GitHub retries; "failed" is re-claimable
    assert rec.finishes == ["failed"]
    assert rec.enqueues == []
