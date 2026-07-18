from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.distillation import worker
from app.distillation.pipeline import TerminalJobError
from app.job_queue.models import PipelineJob


def raw_job(**over):
    fields = dict(
        _id=PydanticObjectId(),
        orgId=PydanticObjectId(),
        repoId=PydanticObjectId(),
        prNumber=7,
        headSha="abc123",
        headBranch="feat/x",
        baseBranch="main",
        authorUserId=None,
        prAuthorGithub="someone",
        deliveryId="d-1",
        installationId=42,
        status="running",
        attempts=1,
        createdAt=datetime.now(timezone.utc),
    )
    fields.update(over)
    return fields


@pytest.fixture
def collection(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(
        PipelineJob, "get_pymongo_collection", classmethod(lambda cls: fake)
    )
    return fake


async def test_claim_is_atomic_oldest_first(collection):
    raw = raw_job()
    collection.find_one_and_update.return_value = raw

    job = await worker.claim_next_job()

    assert job is not None
    assert job.id == raw["_id"]
    assert job.status == "running"
    _, kwargs = collection.find_one_and_update.call_args
    args, _ = collection.find_one_and_update.call_args
    assert args[0] == {"status": "queued"}
    assert args[1] == {"$set": {"status": "running"}, "$inc": {"attempts": 1}}
    assert kwargs["sort"] == [("createdAt", 1)]


async def test_claim_empty_queue(collection):
    collection.find_one_and_update.return_value = None
    assert await worker.claim_next_job() is None


async def test_transient_failure_requeues_below_max(collection):
    job = PipelineJob.model_validate(raw_job(attempts=1))

    await worker.record_failure(job, RuntimeError("blip"))

    (filter_dict, update), _ = collection.update_one.call_args
    assert filter_dict == {"_id": job.id}
    assert update["$set"]["status"] == "queued"
    assert "blip" in update["$set"]["error"]


async def test_transient_failure_parks_at_max_attempts(collection):
    job = PipelineJob.model_validate(raw_job(attempts=worker.MAX_ATTEMPTS))

    await worker.record_failure(job, RuntimeError("blip"))

    (_, update), _ = collection.update_one.call_args
    assert update["$set"]["status"] == "failed"


async def test_terminal_failure_never_requeues(collection):
    job = PipelineJob.model_validate(raw_job(attempts=1))

    await worker.record_failure(job, TerminalJobError("org gone"))

    (_, update), _ = collection.update_one.call_args
    assert update["$set"]["status"] == "failed"
    assert "org gone" in update["$set"]["error"]


async def test_startup_requeues_stale_running(collection):
    collection.update_many.return_value.modified_count = 2

    assert await worker.requeue_stale_running() == 2

    (filter_dict, update), _ = collection.update_many.call_args
    assert filter_dict == {"status": "running"}
    assert update == {"$set": {"status": "queued"}}


def test_worker_toggle(monkeypatch):
    monkeypatch.delenv("PIPELINE_WORKER_ENABLED", raising=False)
    assert worker.worker_enabled() is True
    monkeypatch.setenv("PIPELINE_WORKER_ENABLED", "0")
    assert worker.worker_enabled() is False
