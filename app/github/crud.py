import secrets
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from app.github.models import GitHubInstallState
from app.orgs.models import Org, Repo

# Install-state tokens are single-use and short-lived; 15 minutes comfortably
# covers the redirect to GitHub, the install/authorize screens, and the return.
GITHUB_INSTALL_STATE_EXPIRY = timedelta(minutes=15)


async def create_install_state(
    *, org_id: PydanticObjectId, user_id: PydanticObjectId
) -> GitHubInstallState:
    """Mint a single-use state token binding an install redirect to an org/user."""
    state = GitHubInstallState(
        orgId=org_id,
        userId=user_id,
        token=secrets.token_urlsafe(32),
        expiresAt=datetime.now(timezone.utc) + GITHUB_INSTALL_STATE_EXPIRY,
    )
    await state.insert()
    return state


async def get_install_state(token: str) -> GitHubInstallState | None:
    """Retrieve an install-state token."""
    return await GitHubInstallState.find_one(GitHubInstallState.token == token)


async def delete_install_state(state: GitHubInstallState) -> None:
    """Consume an install-state token."""
    await state.delete()


async def bind_github_installation(org: Org, installation_id: int) -> Org:
    """Bind a GitHub App installation id onto an org. Raises DuplicateKeyError
    (via the sparse-unique index) if the installation is already bound to
    another org — callers map that to a 409."""
    org.githubInstallationId = installation_id
    await org.save()
    return org


async def clear_github_installation(installation_id: int) -> Org | None:
    """Unbind an installation from whichever org holds it and deactivate its
    repos (installation deleted/suspended on GitHub's side). Nulls the
    tenant-resolution key so the org no longer resolves to this installation.
    Returns the affected org, if any."""
    org = await Org.find_one(Org.githubInstallationId == installation_id)
    if org is None:
        return None
    org.githubInstallationId = None
    await org.save()
    await deactivate_all_repos_for_org(org.id)
    return org


async def deactivate_all_repos_for_org(org_id: PydanticObjectId) -> int:
    """Mark every one of an org's repos inactive (App uninstalled). The records
    are kept — deactivation is soft — so history and re-connect stay intact.
    Returns the number affected."""
    result = await Repo.find(Repo.orgId == org_id, Repo.active == True).update(  # noqa: E712
        {"$set": {Repo.active: False}}
    )
    return getattr(result, "modified_count", 0)


async def get_org_by_installation(installation_id: int) -> Org | None:
    """Resolve the org bound to a GitHub App installation id."""
    return await Org.find_one(Org.githubInstallationId == installation_id)


async def sync_repos_for_org(org_id: PydanticObjectId, repos: list[dict]) -> int:
    """Upsert GitHub repos (as returned by the REST API) into an org's Repo
    collection, keyed by githubRepoId. Connecting or re-seeing a repo marks it
    active. Returns the number of new repos."""
    created = 0
    for repo in repos:
        github_repo_id = repo["id"]
        owner = repo["owner"]["login"]
        name = repo["name"]
        default_branch = repo.get("default_branch") or "main"
        existing = await Repo.find_one(
            Repo.orgId == org_id, Repo.githubRepoId == github_repo_id
        )
        if existing is None:
            await Repo(
                orgId=org_id,
                githubRepoId=github_repo_id,
                owner=owner,
                name=name,
                defaultBranch=default_branch,
                active=True,
            ).insert()
            created += 1
        else:
            existing.owner = owner
            existing.name = name
            existing.defaultBranch = default_branch
            existing.active = True
            await existing.save()
    return created


async def deactivate_repos_by_github_ids(
    org_id: PydanticObjectId, github_repo_ids: list[int]
) -> int:
    """Soft-deactivate an org's repos by githubRepoId (repos dropped from the
    installation). Records are kept, not deleted. Returns the number affected."""
    deactivated = 0
    for github_repo_id in github_repo_ids:
        repo = await Repo.find_one(
            Repo.orgId == org_id, Repo.githubRepoId == github_repo_id
        )
        if repo is not None and repo.active:
            repo.active = False
            await repo.save()
            deactivated += 1
    return deactivated
