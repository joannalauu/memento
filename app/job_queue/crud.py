import logging
from datetime import datetime

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.job_queue.models import PipelineJob

logger = logging.getLogger(__name__)


async def enqueue_pipeline_job(
    *,
    org_id: PydanticObjectId,
    repo_id: PydanticObjectId,
    pr_number: int,
    head_sha: str,
    head_branch: str,
    base_branch: str,
    author_user_id: PydanticObjectId | None,
    pr_author_github: str,
    delivery_id: str,
    installation_id: int,
    pr_title: str | None = None,
    pr_url: str | None = None,
    merged_at: datetime | None = None,
) -> PipelineJob | None:
    """Insert a queued job for a merged PR. Returns None when a job for the
    same (repo, PR, head sha) already exists — a redelivery carries a fresh
    deliveryId, so the unique index, not the delivery id, collapses dupes.
    Already-enqueued is a success, not an error."""
    job = PipelineJob(
        orgId=org_id,
        repoId=repo_id,
        prNumber=pr_number,
        headSha=head_sha,
        headBranch=head_branch,
        baseBranch=base_branch,
        authorUserId=author_user_id,
        prAuthorGithub=pr_author_github,
        deliveryId=delivery_id,
        installationId=installation_id,
        prTitle=pr_title,
        prUrl=pr_url,
        mergedAt=merged_at,
    )
    try:
        await job.insert()
        return job
    except DuplicateKeyError:
        logger.info(
            "pipeline job already enqueued repo=%s pr=%s sha=%s",
            repo_id,
            pr_number,
            head_sha,
        )
        return None
