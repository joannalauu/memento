"""In-process PipelineJob consumer.

One asyncio task started from app/lifespan.py — the hackathon-grade stand-in
for a real worker process, mirroring how enqueue_normalization is "the single
seam to swap in a real queue later". Claims are atomic (find_one_and_update on
the status_created index), so adding more processes later is safe; the only
single-worker assumption is the startup requeue of jobs a crash left in
"running". Every iteration is fenced — the loop must never die to one bad job.

Disable with PIPELINE_WORKER_ENABLED=0 (tests do).
"""

import asyncio
import logging
import os

from pymongo import ReturnDocument

from app.distillation.pipeline import (
    TerminalJobError,
    run_pipeline_job,
)
from app.job_queue.models import PipelineJob

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5.0  # seconds between polls when the queue is empty
MAX_ATTEMPTS = 3  # attempts is incremented at claim time


def worker_enabled() -> bool:
    return os.getenv("PIPELINE_WORKER_ENABLED", "1") != "0"


async def claim_next_job() -> PipelineJob | None:
    """Atomically claim the oldest queued job (queued → running, attempts++)."""
    raw = await PipelineJob.get_pymongo_collection().find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running"}, "$inc": {"attempts": 1}},
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return PipelineJob.model_validate(raw) if raw else None


async def requeue_stale_running() -> int:
    """Startup recovery: jobs a crash left claimed go back to the queue.
    Single-worker assumption — with concurrent workers this would steal
    live jobs."""
    result = await PipelineJob.get_pymongo_collection().update_many(
        {"status": "running"}, {"$set": {"status": "queued"}}
    )
    if result.modified_count:
        logger.info("requeued %d stale running job(s)", result.modified_count)
    return result.modified_count


async def record_failure(job: PipelineJob, exc: BaseException) -> None:
    """Failed attempt bookkeeping: requeue transient failures until
    MAX_ATTEMPTS, park terminal/exhausted ones as failed (visible to the
    admin health view alongside outcome="no_sessions")."""
    terminal = isinstance(exc, TerminalJobError) or job.attempts >= MAX_ATTEMPTS
    await PipelineJob.get_pymongo_collection().update_one(
        {"_id": job.id},
        {
            "$set": {
                "status": "failed" if terminal else "queued",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        },
    )


async def worker_loop(app) -> None:
    """Poll–claim–run forever. Cancellation (shutdown) propagates; everything
    else is logged and the loop keeps going."""
    db = await app.state.config.db.get_db()
    bb = app.state.backboard
    gh = app.state.github
    logger.info("pipeline worker started (poll every %.0fs)", POLL_INTERVAL)
    try:
        await requeue_stale_running()
    except Exception:
        logger.exception("startup requeue failed; continuing")
    while True:
        try:
            job = await claim_next_job()
            if job is None:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            logger.info(
                "claimed job %s (repo=%s pr=%s attempt %d)",
                job.id,
                job.repoId,
                job.prNumber,
                job.attempts,
            )
            try:
                await run_pipeline_job(job, db=db, bb=bb, gh=gh)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("job %s failed", job.id)
                await record_failure(job, exc)
        except asyncio.CancelledError:
            logger.info("pipeline worker stopping")
            raise
        except Exception:
            logger.exception("pipeline worker iteration failed")
            await asyncio.sleep(POLL_INTERVAL)
