"""Structured commit-history access for one repo.

The `GitHubToolset` (app/github/tools.py) speaks to the *model* — every method
returns a rendered string. `RepoHistory` speaks to *code*: it returns typed data
(commit dates, lists of commit shas) so callers like the context engine's
staleness check can reason over history instead of parsing prose.

Both are bound to a single repo and share the same installation-token plumbing
via `GitHubApp.rest`. History below a commit is immutable, so every answer here
is cacheable per (sha, path) — see the caching note in the staleness ticket.
"""

from datetime import datetime

from app.github.client import GitHubApp, GitHubError
from app.orgs.models import Org, Repo


def _parse_iso(value: object) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (``...Z``), or None if absent/malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class RepoHistory:
    """Typed commit-history reads for one repo. GitHub errors propagate as
    ``GitHubError`` — callers decide whether a history gap is fatal."""

    PER_PAGE = 100
    MAX_PAGES = 5  # bound: a path with >500 commits since the base reports a floor

    def __init__(
        self,
        gh: GitHubApp,
        *,
        installation_id: int,
        owner: str,
        repo: str,
        default_branch: str,
    ) -> None:
        self._gh = gh
        self._installation_id = installation_id
        self.owner = owner
        self.repo = repo
        self.default_branch = default_branch

    async def _rest(self, method: str, path: str, **kwargs: object):
        return await self._gh.rest(
            method, path, installation_id=self._installation_id, **kwargs
        )

    async def commit_date(self, sha: str) -> datetime | None:
        """The committer date of ``sha``, or None if the commit is unknown
        (e.g. it was garbage-collected or never existed)."""
        try:
            resp = await self._rest(
                "GET", f"/repos/{self.owner}/{self.repo}/commits/{sha}"
            )
        except GitHubError as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        commit = resp.json().get("commit") or {}
        return _parse_iso((commit.get("committer") or {}).get("date"))

    async def commits_touching_path_since(
        self, path: str, *, since: datetime, ref: str | None = None
    ) -> list[str]:
        """Shas of commits on ``ref`` (default branch) that touched ``path`` and
        landed strictly after ``since``, newest first.

        Empty means the path has not moved since ``since``. ``since`` is the base
        commit's own date, so the equal-timestamp base commit is excluded by the
        strict ``> since`` filter. Paging is capped at ``MAX_PAGES``; a hotter
        path reports a floor, which is still a truthy "changed" signal."""
        shas: list[str] = []
        for page in range(1, self.MAX_PAGES + 1):
            resp = await self._rest(
                "GET",
                f"/repos/{self.owner}/{self.repo}/commits",
                params={
                    "path": path,
                    "sha": ref or self.default_branch,
                    "since": since.isoformat(),
                    "per_page": self.PER_PAGE,
                    "page": page,
                },
            )
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                sha = item.get("sha")
                when = _parse_iso(
                    ((item.get("commit") or {}).get("committer") or {}).get("date")
                )
                # `since` is inclusive on GitHub's side; keep only strictly-after
                # so the base commit itself (equal timestamp) never counts.
                if sha and when is not None and when > since:
                    shas.append(sha)
            if len(batch) < self.PER_PAGE:
                break
        return shas


def build_repo_history(org: Org, repo: Repo, gh: GitHubApp) -> RepoHistory:
    """Bind a `RepoHistory` to one org+repo, mirroring `build_github_toolset`'s
    guards: no installation or a deactivated repo can't be queried."""
    if org.githubInstallationId is None:
        raise GitHubError(f"Org '{org.slug}' has no GitHub App installation")
    if not repo.active:
        raise GitHubError(
            f"Repo '{repo.owner}/{repo.name}' is not connected "
            "(GitHub App uninstalled or repo removed from the installation)"
        )
    return RepoHistory(
        gh,
        installation_id=org.githubInstallationId,
        owner=repo.owner,
        repo=repo.name,
        default_branch=repo.defaultBranch,
    )
