import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from app.claude_hook.crud import claim_webhook_event, finish_webhook_event
from app.github.client import GitHubApp, get_github
from app.github.crud import (
    bind_github_installation,
    clear_github_installation,
    deactivate_repos_by_github_ids,
    delete_install_state,
    get_install_state,
    get_org_by_installation,
    get_repo_by_github_id,
    sync_repos_for_org,
)
from app.job_queue.crud import enqueue_pipeline_job
from app.orgs.crud import get_org
from app.orgs.models import Org, User

logger = logging.getLogger(__name__)

router = APIRouter()


async def _best_effort_repo_sync(
    github: GitHubApp, org: Org, installation_id: int
) -> None:
    """Populate an org's repos from the installation. Best-effort: a GitHub
    failure here must not fail the install/webhook — the binding is what
    matters, and repos re-sync on the next installation_repositories event."""
    assert org.id is not None
    try:
        repos = await github.list_installation_repos(installation_id)
        await sync_repos_for_org(org.id, repos)
    except Exception:  # noqa: BLE001 - deliberately swallowed, logged below
        logger.exception(
            "GitHub repo sync failed for org=%s installation=%s",
            org.id,
            installation_id,
        )


@router.get("/setup")
async def github_setup_callback(
    installation_id: int,
    state: str,
    setup_action: str | None = None,
    github: GitHubApp = Depends(get_github),
):
    """GitHub App setup-URL callback. Validates the attribution state minted on
    ``/orgs/{org_id}/github/connect``, binds the installation onto that org, and
    kicks off a best-effort repo sync.

    This is a browser redirect from GitHub, so it carries no bearer token — the
    single-use ``state`` is the proof of both which org and that an admin
    initiated it. It is re-checked against the org's current admins here."""
    install_state = await get_install_state(state)
    if install_state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired installation state",
        )
    # The TTL sweep lags; reject an expired-but-not-yet-reaped state explicitly.
    if install_state.expiresAt < datetime.now(timezone.utc):
        await delete_install_state(install_state)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Installation state has expired",
        )

    org = await get_org(install_state.orgId)
    if org is None:
        await delete_install_state(install_state)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    # Re-verify the initiating user is still an admin of the org.
    is_admin = any(
        m.userId == install_state.userId and m.role == "admin" for m in org.members
    )
    if not is_admin:
        await delete_install_state(install_state)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Initiating user is no longer an org admin",
        )

    try:
        await bind_github_installation(org, installation_id)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This GitHub installation is already bound to another org",
        )
    await delete_install_state(install_state)
    await _best_effort_repo_sync(github, org, installation_id)

    if github.settings.post_install_redirect_url:
        return RedirectResponse(
            url=github.settings.post_install_redirect_url,
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {
        "orgId": str(org.id),
        "installationId": installation_id,
        "setupAction": setup_action,
    }


def _parse_github_ts(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (trailing Z); None on absent/bad."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/webhook")
async def github_webhook(
    request: Request,
    github: GitHubApp = Depends(get_github),
):
    """Handle GitHub App webhooks.

    Lifecycle (installation deleted/suspended) and repo membership changes keep
    their inline handling; a merged PR (``pull_request`` closed with
    ``merged=true``) enqueues a durable PipelineJob for the distillation worker
    instead of processing inline — the handler's job ends at "validated,
    deduped, enqueued". Deliveries are deduped on X-GitHub-Delivery via
    WebhookEvent's unique index (GitHub is at-least-once). Binding itself
    happens on the setup callback, not here — a webhook can't tell us which org
    an install belongs to until it's bound."""
    body = await request.body()
    if not github.verify_webhook(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)

    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not delivery_id:
        # GitHub always sends the header; a signed body without it is a
        # synthetic caller. Nothing to dedupe on, so drop it.
        logger.warning("webhook delivery without X-GitHub-Delivery, event=%s", event)
        return {"ok": True, "skipped": True}

    record = await claim_webhook_event(delivery_id, event, payload)
    if record is None:
        return {"ok": True, "duplicate": True}

    try:
        outcome = await _process_webhook(github, event, payload, delivery_id)
    except Exception:
        logger.exception(
            "webhook processing failed delivery=%s event=%s", delivery_id, event
        )
        try:
            await finish_webhook_event(record, "failed")
        except Exception:  # noqa: BLE001 - best-effort; the 500 already retries
            logger.exception(
                "could not mark webhook event failed delivery=%s", delivery_id
            )
        # Non-2xx makes GitHub retry; the "failed" record is re-claimable.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        )

    await finish_webhook_event(record, outcome)
    return {"ok": True}


async def _process_webhook(
    github: GitHubApp, event: str, payload: dict, delivery_id: str
) -> Literal["processed", "skipped"]:
    """Route one claimed delivery. Returns the WebhookEvent outcome status:
    "processed" for handled events, "skipped" for everything ignored."""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")

    if event == "installation":
        action = payload.get("action")
        if action in ("deleted", "suspend") and installation_id is not None:
            await clear_github_installation(installation_id)
        return "processed"

    if event == "installation_repositories":
        if installation_id is not None:
            org = await get_org_by_installation(installation_id)
            if org is not None:
                assert org.id is not None
                # Added repos in the payload lack default_branch, so re-list
                # from the API to upsert full records.
                if payload.get("repositories_added"):
                    await _best_effort_repo_sync(github, org, installation_id)
                removed_ids = [r["id"] for r in payload.get("repositories_removed", [])]
                if removed_ids:
                    await deactivate_repos_by_github_ids(org.id, removed_ids)
        return "processed"

    # The CI hook: only a merged PR close triggers the pipeline. Everything
    # else (opened, synchronize, labeled, ping, push, …) is ignored.
    pr = payload.get("pull_request") or {}
    if not (
        event == "pull_request"
        and payload.get("action") == "closed"
        and pr.get("merged") is True
    ):
        return "skipped"

    if installation_id is None:
        logger.warning("merged-PR delivery without installation id: %s", delivery_id)
        return "skipped"
    org = await get_org_by_installation(installation_id)
    if org is None:
        logger.info("merged-PR delivery for unbound installation=%s", installation_id)
        return "skipped"
    assert org.id is not None
    repo = await get_repo_by_github_id(org.id, payload["repository"]["id"])
    if repo is None or not repo.active:
        logger.info(
            "merged-PR delivery for unknown/inactive repo github_id=%s org=%s",
            payload["repository"]["id"],
            org.id,
        )
        return "skipped"
    assert repo.id is not None

    # Author linkage is best-effort: githubUsername is sparse, and the
    # downstream fallback flow works from the raw login.
    author_login = ((pr.get("user") or {}).get("login") or "").lower()
    author = (
        await User.find_one({"githubUsername": author_login}) if author_login else None
    )

    await enqueue_pipeline_job(
        org_id=org.id,
        repo_id=repo.id,
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        head_branch=pr["head"]["ref"],
        base_branch=pr["base"]["ref"],
        author_user_id=author.id if author else None,
        pr_author_github=author_login,
        delivery_id=delivery_id,
        installation_id=installation_id,
        pr_title=pr.get("title"),
        pr_url=pr.get("html_url"),
        merged_at=_parse_github_ts(pr.get("merged_at")),
    )
    return "processed"
