"""GitHubToolset tests — MockTransport GitHub, no network. Token minting is
bypassed by monkeypatching installation_token."""

import base64
import json

import httpx
import pytest

from app.github.client import GitHubApp, GitHubError, GitHubSettings
from app.github.tools import (
    GITHUB_TOOL_DEFINITIONS,
    GitHubToolset,
    build_github_toolset,
)
from app.orgs.models import Org, Repo

OWNER, REPO, BRANCH = "acme", "api", "main"


def make_toolset(handler) -> GitHubToolset:
    gh = GitHubApp(GitHubSettings(app_id="12345", private_key="unused"))
    gh._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Accept": "application/vnd.github+json"},
    )

    async def fake_token(installation_id, *, force_refresh=False):
        return "tok"

    gh.installation_token = fake_token
    return GitHubToolset(
        gh, installation_id=42, owner=OWNER, repo=REPO, default_branch=BRANCH
    )


# ─── get_file ─────────────────────────────────────────────────────────────────


async def test_get_file_decodes_base64_and_passes_ref():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        content = base64.b64encode("print('hi')\n".encode()).decode()
        return httpx.Response(
            200, json={"encoding": "base64", "content": content, "path": "x.py"}
        )

    ts = make_toolset(handler)
    out = await ts.get_file("x.py", ref="abc123")
    assert out == "print('hi')\n"
    assert f"/repos/{OWNER}/{REPO}/contents/x.py" in seen["url"]
    assert "ref=abc123" in seen["url"]


async def test_get_file_directory_returns_error_string():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"path": "app/a.py"}, {"path": "app/b.py"}])

    ts = make_toolset(handler)
    out = await ts.get_file("app")
    assert out.startswith("Error: 'app' is a directory")
    assert "app/a.py" in out and "app/b.py" in out


async def test_get_file_large_falls_back_to_raw():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Accept", ""))
        if "raw" in request.headers.get("Accept", ""):
            return httpx.Response(200, text="raw file body")
        return httpx.Response(200, json={"encoding": "none", "content": ""})

    ts = make_toolset(handler)
    out = await ts.get_file("big.bin")
    assert out == "raw file body"
    assert len(calls) == 2
    assert "raw" in calls[1]


async def test_get_file_not_found_is_error_string():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    ts = make_toolset(handler)
    out = await ts.get_file("nope.py")
    assert out.startswith("Error:")


# ─── list_tree ────────────────────────────────────────────────────────────────


async def test_list_tree_resolves_ref_and_filters_prefix():
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"commit": {"tree": {"sha": "treesha1"}}})
        return httpx.Response(
            200,
            json={
                "truncated": True,
                "tree": [
                    {"path": "app/main.py", "type": "blob", "size": 10},
                    {"path": "app/orgs/models.py", "type": "blob", "size": 20},
                    {"path": "README.md", "type": "blob", "size": 5},
                ],
            },
        )

    ts = make_toolset(handler)
    out = await ts.list_tree(path="app")
    assert f"/repos/{OWNER}/{REPO}/commits/{BRANCH}" in urls[0]
    assert "/git/trees/treesha1" in urls[1] and "recursive=1" in urls[1]
    assert "WARNING: tree truncated" in out
    assert "app/main.py" in out and "app/orgs/models.py" in out
    assert "README.md" not in out


# ─── search_code ──────────────────────────────────────────────────────────────


async def test_search_code_scopes_repo_and_text_match_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["q"] = request.url.params["q"]
        seen["accept"] = request.headers["Accept"]
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "path": "app/main.py",
                        "text_matches": [{"fragment": "def register_routes"}],
                    }
                ],
            },
        )

    ts = make_toolset(handler)
    out = await ts.search_code("register_routes")
    assert seen["q"].startswith(f"repo:{OWNER}/{REPO} ")
    assert "text-match" in seen["accept"]
    assert "app/main.py" in out and "def register_routes" in out


# ─── get_pr_diff ──────────────────────────────────────────────────────────────


async def test_get_pr_diff_paginates():
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        if page == 1:
            files = [
                {
                    "filename": f"f{i}.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": f"@@ f{i}",
                }
                for i in range(100)
            ]
        else:
            files = [
                {
                    "filename": "last.py",
                    "status": "added",
                    "additions": 3,
                    "deletions": 0,
                }  # no patch → binary/too-large marker
            ]
        return httpx.Response(200, json=files)

    ts = make_toolset(handler)
    out = await ts.get_pr_diff(7)
    assert pages == [1, 2]
    assert "f0.py" in out and "f99.py" in out and "last.py" in out
    assert "[no patch — binary or too large]" in out


# ─── get_blame ────────────────────────────────────────────────────────────────


async def test_get_blame_graphql_and_client_side_slicing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["query"] = body["query"]
        seen["variables"] = body["variables"]

        def rng(s, e, oid, who):
            return {
                "startingLine": s,
                "endingLine": e,
                "commit": {
                    "oid": oid,
                    "committedDate": "2026-07-01T12:00:00Z",
                    "messageHeadline": "some change",
                    "author": {"name": who, "email": "", "user": {"login": who}},
                },
            }

        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "object": {
                            "blame": {
                                "ranges": [
                                    rng(1, 5, "aaaaaaaa1111", "alice"),
                                    rng(6, 20, "bbbbbbbb2222", "bob"),
                                    rng(21, 40, "cccccccc3333", "carol"),
                                ]
                            }
                        }
                    }
                }
            },
        )

    ts = make_toolset(handler)
    out = await ts.get_blame("app/main.py", 10, 25)
    assert "blame(path: $path)" in seen["query"]
    assert seen["variables"] == {
        "owner": OWNER,
        "name": REPO,
        "expression": BRANCH,
        "path": "app/main.py",
    }
    lines = out.splitlines()
    assert len(lines) == 2  # range 1-5 excluded
    assert lines[0].startswith("L10-L20") and "bob" in lines[0]
    assert lines[1].startswith("L21-L25") and "carol" in lines[1]
    assert "alice" not in out


async def test_get_blame_bad_ref_is_error_string():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"repository": {"object": None}}})

    ts = make_toolset(handler)
    out = await ts.get_blame("x.py", 1, 5, ref="ghost")
    assert out.startswith("Error:") and "ghost" in out


# ─── build_github_toolset ─────────────────────────────────────────────────────


def _org(installation_id):
    return Org.model_construct(
        name="Acme", slug="acme", githubInstallationId=installation_id
    )


def _repo():
    return Repo.model_construct(
        owner=OWNER, name=REPO, defaultBranch=BRANCH, githubRepoId=1
    )


def test_build_github_toolset_requires_installation():
    gh = GitHubApp(GitHubSettings(app_id="12345"))
    with pytest.raises(GitHubError, match="no GitHub App installation"):
        build_github_toolset(_org(None), _repo(), gh)


async def test_build_github_toolset_registry_matches_definitions():
    gh = GitHubApp(GitHubSettings(app_id="12345"))
    definitions, registry = build_github_toolset(_org(42), _repo(), gh)
    def_names = {d["function"]["name"] for d in definitions}
    assert (
        def_names
        == set(registry)
        == {
            "get_file",
            "list_tree",
            "search_code",
            "get_pr_diff",
            "get_blame",
        }
    )
    assert definitions is GITHUB_TOOL_DEFINITIONS
    await gh.aclose()
