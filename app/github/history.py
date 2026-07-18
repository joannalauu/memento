"""Structured commit-history access for one repo.

The `GitHubToolset` (app/github/tools.py) speaks to the *model* — every method
returns a rendered string. `RepoHistory` speaks to *code*: it returns typed data
(commit dates, lists of commit shas) so callers like the context engine's
staleness check can reason over history instead of parsing prose.

Both are bound to a single repo and share the same installation-token plumbing
via `GitHubApp.rest`. The diff between two fixed commits never changes, so
`changed_since` memoizes its result on the immutable (owner, repo, base_sha,
head_sha, path) key indefinitely — a new commit advances head_sha and mints a
fresh key rather than invalidating an old one. This is the process-level cache
the staleness ticket calls for.
"""

from datetime import datetime

from app.github.client import GitHubApp, GitHubError
from app.orgs.models import Org, Repo

# Process-wide cache: (owner, repo, base_sha, head_sha, path) -> commit shas that
# touched `path` in (base_sha, head_sha]. Both endpoints are fixed commits, so the
# answer is immutable and kept indefinitely. Cleared wholesale when it hits the
# cap — crude, but memory stays bounded and a cold miss just re-fetches.
_CHANGE_CACHE: dict[tuple[str, str, str, str, str], list[str]] = {}
_CACHE_MAX = 50_000


def clear_history_cache() -> None:
    """Drop the whole change cache (test hook; also the cap-eviction path)."""
    _CHANGE_CACHE.clear()


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
        # Resolved branch tips this instance has already looked up. Branch tips
        # move, so this is a per-instance memo (one lookup per sweep/request),
        # never the durable cross-request cache — that's _CHANGE_CACHE.
        self._head: dict[str, str] = {}

    async def _rest(self, method: str, path: str, **kwargs: object):
        return await self._gh.rest(
            method, path, installation_id=self._installation_id, **kwargs
        )

    async def head_sha(self, ref: str | None = None) -> str:
        """Current commit sha at the tip of ``ref`` (default branch). Memoized on
        the instance so one check/sweep resolves each ref only once."""
        key = ref or self.default_branch
        if key not in self._head:
            resp = await self._rest(
                "GET", f"/repos/{self.owner}/{self.repo}/commits/{key}"
            )
            self._head[key] = resp.json()["sha"]
        return self._head[key]

    async def changed_since(
        self, path: str, *, base_sha: str, base_date: datetime, head_sha: str
    ) -> list[str]:
        """Commit shas that touched ``path`` between ``base_sha`` (exclusive) and
        ``head_sha`` (inclusive), newest first — empty means it hasn't moved.

        Cached indefinitely on the immutable (base, head, path) triple: the range
        between two fixed commits can never change. ``base_date`` is the base
        commit's own timestamp, used only to bound the underlying query."""
        cache_key = (self.owner, self.repo, base_sha, head_sha, path)
        cached = _CHANGE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        # Query against the resolved head sha (not a moving branch name) so the
        # fetched range matches the immutable cache key.
        shas = await self.commits_touching_path_since(
            path, since=base_date, ref=head_sha
        )
        if len(_CHANGE_CACHE) >= _CACHE_MAX:
            clear_history_cache()
        _CHANGE_CACHE[cache_key] = shas
        return shas

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
