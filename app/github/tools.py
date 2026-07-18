"""
GitHub tool suite for the Backboard executor loop (app/backboard/executor.py).

A `GitHubToolset` is bound to ONE repo at construction; every method returns a
model-facing string (GitHub errors are rendered as "Error: ..." strings so the
model can react instead of the run crashing). `build_github_toolset` adapts a
toolset into the (tool_definitions, registry) pair the executor consumes.
"""

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from app.github.client import GitHubApp, GitHubError
from app.orgs.models import Org, Repo

ToolFn = Callable[[dict[str, Any]], Awaitable[str]]

# Cap on any single tool output handed to the model.
MAX_TOOL_OUTPUT = 60_000
TRUNCATION_MARKER = "\n[truncated]"

BLAME_QUERY = """
query Blame($owner: String!, $name: String!, $expression: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $expression) {
      ... on Commit {
        blame(path: $path) {
          ranges {
            startingLine
            endingLine
            commit {
              oid
              committedDate
              messageHeadline
              author { name email user { login } }
            }
          }
        }
      }
    }
  }
}
"""


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + TRUNCATION_MARKER
    return text


class GitHubToolset:
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

    async def _rest(self, method: str, path: str, **kwargs: Any):
        return await self._gh.rest(
            method, path, installation_id=self._installation_id, **kwargs
        )

    # ─── Tools ────────────────────────────────────────────────────────────────

    async def get_file(self, path: str, ref: str | None = None) -> str:
        params = {"ref": ref} if ref else None
        try:
            resp = await self._rest(
                "GET", f"/repos/{self.owner}/{self.repo}/contents/{path}", params=params
            )
            data = resp.json()
            if isinstance(data, list):
                entries = "\n".join(e.get("path", "?") for e in data)
                return f"Error: '{path}' is a directory. Entries:\n{entries}"
            if data.get("encoding") == "base64" and data.get("content"):
                content = base64.b64decode(data["content"]).decode(
                    "utf-8", errors="replace"
                )
            else:
                # Large files (1-100 MB) come back with encoding "none" and no
                # content — re-fetch as raw media.
                raw = await self._rest(
                    "GET",
                    f"/repos/{self.owner}/{self.repo}/contents/{path}",
                    params=params,
                    headers={"Accept": "application/vnd.github.raw+json"},
                )
                content = raw.text
            return _truncate(content)
        except GitHubError as exc:
            return f"Error: {exc}"

    async def list_tree(self, path: str | None = None, ref: str | None = None) -> str:
        try:
            commit = await self._rest(
                "GET",
                f"/repos/{self.owner}/{self.repo}/commits/{ref or self.default_branch}",
            )
            tree_sha = commit.json()["commit"]["tree"]["sha"]
            resp = await self._rest(
                "GET",
                f"/repos/{self.owner}/{self.repo}/git/trees/{tree_sha}",
                params={"recursive": "1"},
            )
            data = resp.json()
            entries = data.get("tree", [])
            if path:
                prefix = path.rstrip("/") + "/"
                entries = [
                    e
                    for e in entries
                    if e["path"] == path.rstrip("/") or e["path"].startswith(prefix)
                ]
            lines = []
            if data.get("truncated"):
                lines.append(
                    "WARNING: tree truncated by GitHub (>100k entries); "
                    "listing is incomplete."
                )
            for e in entries:
                size = (
                    f", {e['size']} B"
                    if e.get("type") == "blob" and "size" in e
                    else ""
                )
                lines.append(f"{e['path']}  ({e.get('type', '?')}{size})")
            if not entries:
                lines.append(
                    f"No entries found under '{path}'." if path else "Empty tree."
                )
            return _truncate("\n".join(lines))
        except GitHubError as exc:
            return f"Error: {exc}"

    async def search_code(self, query: str) -> str:
        try:
            resp = await self._rest(
                "GET",
                "/search/code",
                params={"q": f"repo:{self.owner}/{self.repo} {query}", "per_page": 20},
                headers={"Accept": "application/vnd.github.text-match+json"},
            )
            data = resp.json()
            items = data.get("items", [])
            lines = [f"{data.get('total_count', len(items))} result(s) for {query!r}:"]
            for item in items:
                lines.append(f"\n{item['path']}")
                for match in (item.get("text_matches") or [])[:2]:
                    fragment = match.get("fragment", "").strip()
                    if fragment:
                        lines.append(f"  | {fragment}")
            return _truncate("\n".join(lines))
        except GitHubError as exc:
            return f"Error: {exc}"

    async def get_pr_diff(self, pr_number: int) -> str:
        try:
            chunks: list[str] = []
            page = 1
            while True:
                resp = await self._rest(
                    "GET",
                    f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/files",
                    params={"per_page": 100, "page": page},
                )
                files = resp.json()
                for f in files:
                    chunks.append(
                        f"--- {f['filename']} ({f.get('status', '?')}, "
                        f"+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
                    )
                    chunks.append(f.get("patch") or "[no patch — binary or too large]")
                if len(files) < 100:
                    break
                page += 1
            if not chunks:
                return f"PR #{pr_number} has no changed files."
            return _truncate("\n".join(chunks))
        except GitHubError as exc:
            return f"Error: {exc}"

    async def get_blame(
        self, path: str, start: int, end: int, ref: str | None = None
    ) -> str:
        try:
            data = await self._gh.graphql(
                BLAME_QUERY,
                {
                    "owner": self.owner,
                    "name": self.repo,
                    "expression": ref or self.default_branch,
                    "path": path,
                },
                installation_id=self._installation_id,
            )
            obj = (data.get("repository") or {}).get("object")
            if not obj:
                return f"Error: ref '{ref or self.default_branch}' not found in repo."
            blame = obj.get("blame")
            if not blame or not blame.get("ranges"):
                return f"Error: no blame data for '{path}' — does the file exist at this ref?"
            # GitHub's blame API has no line filtering — slice ranges client-side.
            lines = []
            for r in blame["ranges"]:
                s, e = r["startingLine"], r["endingLine"]
                if s > end or e < start:
                    continue
                commit = r["commit"]
                author = commit.get("author") or {}
                user = author.get("user") or {}
                who = user.get("login") or author.get("name") or "unknown"
                lines.append(
                    f"L{max(start, s)}-L{min(end, e)}  {commit['oid'][:8]}  "
                    f"{(commit.get('committedDate') or '')[:10]}  {who}  "
                    f"{commit.get('messageHeadline', '')}"
                )
            if not lines:
                return f"Error: lines {start}-{end} not found in '{path}' at this ref."
            return _truncate("\n".join(lines))
        except GitHubError as exc:
            return f"Error: {exc}"


GITHUB_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_file",
            "description": (
                "Read a file's full contents from the repository. "
                "Returns the decoded text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo-relative file path, e.g. 'app/main.py'",
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Branch, tag, or commit SHA. Defaults to the default branch."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tree",
            "description": (
                "List all files in the repository (recursive). "
                "Optionally filter to a directory prefix."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory prefix filter, e.g. 'app/orgs'",
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Branch, tag, or commit SHA. Defaults to the default branch."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search code in this repository (default branch only). Supports "
                "GitHub code-search qualifiers like path: and language:."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search terms; the repo qualifier is added automatically."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pr_diff",
            "description": (
                "Get the diff of a pull request: changed files with per-file "
                "unified patches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "description": "Pull request number.",
                    },
                },
                "required": ["pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_blame",
            "description": (
                "Git blame for a line range of a file: who last touched each "
                "line range, in which commit, and when."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo-relative file path.",
                    },
                    "start": {
                        "type": "integer",
                        "description": "First line (1-based, inclusive).",
                    },
                    "end": {
                        "type": "integer",
                        "description": "Last line (1-based, inclusive).",
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Branch or commit SHA. Defaults to the default branch."
                        ),
                    },
                },
                "required": ["path", "start", "end"],
            },
        },
    },
]


def build_github_toolset(
    org: Org, repo: Repo, gh: GitHubApp
) -> tuple[list[dict[str, Any]], dict[str, ToolFn]]:
    """Bind the tool suite to one org+repo. Returns (tool_definitions, registry)
    ready for app/backboard/executor.py. Bad model-supplied args (missing keys,
    non-int numbers) raise here and become error outputs in the executor."""
    if org.githubInstallationId is None:
        raise GitHubError(f"Org '{org.slug}' has no GitHub App installation")
    if not repo.active:
        # Deactivated when the App was uninstalled or the repo dropped from the
        # installation (see app/github/crud.py). Its installation token would
        # 401/404 at GitHub anyway — fail fast with a clear reason.
        raise GitHubError(
            f"Repo '{repo.owner}/{repo.name}' is not connected "
            "(GitHub App uninstalled or repo removed from the installation)"
        )
    ts = GitHubToolset(
        gh,
        installation_id=org.githubInstallationId,
        owner=repo.owner,
        repo=repo.name,
        default_branch=repo.defaultBranch,
    )
    registry: dict[str, ToolFn] = {
        "get_file": lambda a: ts.get_file(a["path"], a.get("ref")),
        "list_tree": lambda a: ts.list_tree(a.get("path"), a.get("ref")),
        "search_code": lambda a: ts.search_code(a["query"]),
        "get_pr_diff": lambda a: ts.get_pr_diff(int(a["pr_number"])),
        "get_blame": lambda a: ts.get_blame(
            a["path"], int(a["start"]), int(a["end"]), a.get("ref")
        ),
    }
    return GITHUB_TOOL_DEFINITIONS, registry
