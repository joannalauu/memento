from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.github.client import GitHubError
from app.github.history import (
    RepoHistory,
    build_repo_history,
    clear_history_cache,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_history_cache()
    yield
    clear_history_cache()


def _resp(payload):
    return SimpleNamespace(json=lambda: payload)


def _history(rest):
    gh = SimpleNamespace(rest=rest)
    return RepoHistory(
        gh, installation_id=1, owner="acme", repo="api", default_branch="main"
    )


def _commit(sha, iso):
    return {"sha": sha, "commit": {"committer": {"date": iso}}}


async def test_commit_date_parses_committer_date():
    rest = AsyncMock(
        return_value=_resp({"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}})
    )
    assert await _history(rest).commit_date("abc") == BASE


async def test_commit_date_404_returns_none():
    rest = AsyncMock(side_effect=GitHubError("nope", status_code=404))
    assert await _history(rest).commit_date("gone") is None


async def test_commit_date_other_error_propagates():
    rest = AsyncMock(side_effect=GitHubError("boom", status_code=500))
    with pytest.raises(GitHubError):
        await _history(rest).commit_date("x")


async def test_commits_since_excludes_base_and_older():
    # `since` is inclusive on GitHub's side, so the base (equal timestamp) and an
    # older commit must be filtered out by the strict > since check.
    rest = AsyncMock(
        return_value=_resp(
            [
                _commit("newer", "2026-02-01T00:00:00Z"),
                _commit("base", "2026-01-01T00:00:00Z"),
                _commit("older", "2025-12-01T00:00:00Z"),
            ]
        )
    )
    shas = await _history(rest).commits_touching_path_since("app/a.py", since=BASE)
    assert shas == ["newer"]


async def test_commits_since_paginates_until_short_page():
    page1 = [_commit(f"c{i}", "2026-03-01T00:00:00Z") for i in range(100)]
    page2 = [_commit("c100", "2026-03-02T00:00:00Z")]
    rest = AsyncMock(side_effect=[_resp(page1), _resp(page2)])
    shas = await _history(rest).commits_touching_path_since("app/a.py", since=BASE)
    assert len(shas) == 101
    assert rest.await_count == 2
    # query wiring: path, branch, since, paging
    _, kwargs = rest.call_args_list[0]
    assert kwargs["params"]["path"] == "app/a.py"
    assert kwargs["params"]["sha"] == "main"
    assert kwargs["params"]["since"] == BASE.isoformat()


async def test_commits_since_ref_override_and_empty():
    rest = AsyncMock(return_value=_resp([]))
    shas = await _history(rest).commits_touching_path_since(
        "app/a.py", since=BASE, ref="release"
    )
    assert shas == []
    _, kwargs = rest.call_args
    assert kwargs["params"]["sha"] == "release"


async def test_commits_since_page_cap_bounds_calls():
    full = [_commit(f"c{i}", "2026-03-01T00:00:00Z") for i in range(100)]
    rest = AsyncMock(return_value=_resp(full))  # always a full page
    shas = await _history(rest).commits_touching_path_since("app/a.py", since=BASE)
    assert rest.await_count == RepoHistory.MAX_PAGES
    assert len(shas) == 100 * RepoHistory.MAX_PAGES


# ─── head_sha ─────────────────────────────────────────────────────────────────


async def test_head_sha_resolves_and_memoizes_per_ref():
    rest = AsyncMock(return_value=_resp({"sha": "deadbeef"}))
    h = _history(rest)
    assert await h.head_sha() == "deadbeef"
    assert await h.head_sha() == "deadbeef"
    assert rest.await_count == 1  # memoized on the instance
    await h.head_sha("release")  # a distinct ref is its own memo slot
    assert rest.await_count == 2
    _, kwargs = rest.call_args
    assert "/commits/release" in rest.call_args[0][1]


# ─── changed_since cache ──────────────────────────────────────────────────────


async def test_changed_since_caches_on_immutable_key():
    rest = AsyncMock(return_value=_resp([_commit("newer", "2026-02-01T00:00:00Z")]))
    h = _history(rest)
    kw = dict(base_sha="base", base_date=BASE, head_sha="head")
    first = await h.changed_since("app/a.py", **kw)
    second = await h.changed_since("app/a.py", **kw)
    assert first == second == ["newer"]
    assert rest.await_count == 1  # second call served from cache
    # the underlying query is pinned to the head sha, not a branch name
    _, kwargs = rest.call_args
    assert kwargs["params"]["sha"] == "head"


async def test_changed_since_new_head_is_a_fresh_key():
    rest = AsyncMock(return_value=_resp([]))
    h = _history(rest)
    await h.changed_since("app/a.py", base_sha="base", base_date=BASE, head_sha="h1")
    await h.changed_since("app/a.py", base_sha="base", base_date=BASE, head_sha="h2")
    assert rest.await_count == 2  # head advanced -> new immutable key -> re-fetch


async def test_changed_since_cache_shared_across_instances():
    rest1 = AsyncMock(return_value=_resp([_commit("c", "2026-02-01T00:00:00Z")]))
    rest2 = AsyncMock(return_value=_resp([]))
    kw = dict(base_sha="b", base_date=BASE, head_sha="h")
    assert await _history(rest1).changed_since("p", **kw) == ["c"]
    # a second RepoHistory over the same repo hits the process-level cache
    assert await _history(rest2).changed_since("p", **kw) == ["c"]
    rest2.assert_not_awaited()


async def test_clear_history_cache_forces_refetch():
    rest = AsyncMock(return_value=_resp([]))
    h = _history(rest)
    kw = dict(base_sha="b", base_date=BASE, head_sha="h")
    await h.changed_since("p", **kw)
    clear_history_cache()
    await h.changed_since("p", **kw)
    assert rest.await_count == 2


# ─── build guard ──────────────────────────────────────────────────────────────


def test_build_repo_history_requires_installation():
    org = SimpleNamespace(githubInstallationId=None, slug="acme")
    repo = SimpleNamespace(active=True, owner="acme", name="api", defaultBranch="main")
    with pytest.raises(GitHubError):
        build_repo_history(org, repo, SimpleNamespace())


def test_build_repo_history_requires_active_repo():
    org = SimpleNamespace(githubInstallationId=42, slug="acme")
    repo = SimpleNamespace(active=False, owner="acme", name="api", defaultBranch="main")
    with pytest.raises(GitHubError):
        build_repo_history(org, repo, SimpleNamespace())


def test_build_repo_history_ok():
    org = SimpleNamespace(githubInstallationId=42, slug="acme")
    repo = SimpleNamespace(active=True, owner="acme", name="api", defaultBranch="main")
    history = build_repo_history(org, repo, SimpleNamespace())
    assert history.owner == "acme" and history.default_branch == "main"
