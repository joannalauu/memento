import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from app.github.client import GitHubApp, get_github
from app.github.crud import (
    bind_github_installation,
    clear_github_installation,
    deactivate_repos_by_github_ids,
    delete_install_state,
    get_install_state,
    get_org_by_installation,
    sync_repos_for_org,
)
from app.orgs.crud import get_org
from app.orgs.models import Org

logger = logging.getLogger(__name__)

router = APIRouter()


async def _best_effort_repo_sync(
    github: GitHubApp, org: Org, installation_id: int
) -> None:
    """Populate an org's repos from the installation. Best-effort: a GitHub
    failure here must not fail the install/webhook — the binding is what
    matters, and repos re-sync on the next installation_repositories event."""
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


@router.post("/webhook")
async def github_webhook(
    request: Request,
    github: GitHubApp = Depends(get_github),
):
    """Handle GitHub App webhooks: lifecycle (installation deleted/suspended) and
    repo membership changes. Binding itself happens on the setup callback, not
    here — a webhook can't tell us which org an install belongs to until it's
    bound."""
    body = await request.body()
    if not github.verify_webhook(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")

    if event == "installation":
        action = payload.get("action")
        if action in ("deleted", "suspend") and installation_id is not None:
            await clear_github_installation(installation_id)
    elif event == "installation_repositories" and installation_id is not None:
        org = await get_org_by_installation(installation_id)
        if org is not None:
            # Added repos in the payload lack default_branch, so re-list from the
            # API to upsert full records.
            if payload.get("repositories_added"):
                await _best_effort_repo_sync(github, org, installation_id)
            removed_ids = [r["id"] for r in payload.get("repositories_removed", [])]
            if removed_ids:
                await deactivate_repos_by_github_ids(org.id, removed_ids)

    return {"ok": True}
