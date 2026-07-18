"""
MCP tool registry.

Each tool is auto-scoped to the org resolved from the caller's API key
(request → API key → user + org; see app/api_auth). The model never supplies an
org id — `McpContext.org` is the API key's org, so a key can only ever reach its
own org's data. Handlers reuse the same crud/toolsets the REST routes use.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId

from app.api_auth.dependencies import ApiKeyPrincipal
from app.backboard.client import Backboard
from app.context_engine import (
    check_consistency,
    extract_anchors,
    find_related_context,
)
from app.file_upload.crud import (
    get_document_index_entry,
    list_document_index_entries,
)
from app.github.client import GitHubApp
from app.github.tools import build_github_toolset
from app.orgs.crud import list_org_members, list_repos_for_org
from app.orgs.models import Org, Repo


class McpToolError(Exception):
    """Raised by a tool handler for an expected, user-facing failure (bad args,
    not found). The server renders it as an ``isError`` tool result rather than a
    protocol-level error."""


@dataclass
class McpContext:
    """Per-request context handed to every tool handler."""

    principal: ApiKeyPrincipal
    org: Org
    github: GitHubApp
    backboard: Backboard


ToolHandler = Callable[[McpContext, dict[str, Any]], Awaitable[Any]]


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def definition(self) -> dict[str, Any]:
        """The tools/list entry for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ─── helpers ──────────────────────────────────────────────────────────────────


def _object_id(value: Any) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except Exception as exc:  # noqa: BLE001 - normalize to a tool error
        raise McpToolError(f"Invalid id: {value!r}") from exc


def _require(args: dict[str, Any], key: str) -> Any:
    if key not in args or args[key] in (None, ""):
        raise McpToolError(f"Missing required argument: {key!r}")
    return args[key]


async def _resolve_repo(org: Org, repo_ref: str) -> Repo | None:
    """Resolve a repo within the org by 'owner/name' or bare 'name'."""
    if "/" in repo_ref:
        owner, name = repo_ref.split("/", 1)
        return await Repo.find_one(
            Repo.orgId == org.id, Repo.owner == owner, Repo.name == name
        )
    return await Repo.find_one(Repo.orgId == org.id, Repo.name == repo_ref)


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise McpToolError(f"{key!r} must be an integer") from exc


async def _require_repo(ctx: "McpContext", args: dict[str, Any]) -> Repo:
    repo = await _resolve_repo(ctx.org, _require(args, "repo"))
    if repo is None:
        raise McpToolError(f"Repo {args.get('repo')!r} not found in this org")
    return repo


# ─── handlers ─────────────────────────────────────────────────────────────────


async def _list_repos(ctx: McpContext, args: dict[str, Any]) -> Any:
    repos = await list_repos_for_org(ctx.org.id)
    return [
        {
            "id": str(r.id),
            "owner": r.owner,
            "name": r.name,
            "defaultBranch": r.defaultBranch,
            "active": r.active,
            "githubRepoId": r.githubRepoId,
        }
        for r in repos
    ]


async def _list_members(ctx: McpContext, args: dict[str, Any]) -> Any:
    members = await list_org_members(ctx.org)
    return [m.model_dump(mode="json") for m in members]


async def _list_documents(ctx: McpContext, args: dict[str, Any]) -> Any:
    entries = await list_document_index_entries(ctx.org.id)
    return [
        {
            "id": str(e.id),
            "filename": e.filename,
            "kind": e.kind,
            "status": e.status,
            "createdAt": e.createdAt.isoformat(),
        }
        for e in entries
    ]


async def _get_document(ctx: McpContext, args: dict[str, Any]) -> Any:
    doc_id = _object_id(_require(args, "document_id"))
    entry = await get_document_index_entry(org_id=ctx.org.id, doc_id=doc_id)
    if entry is None:
        raise McpToolError("Document not found")
    return {
        "id": str(entry.id),
        "filename": entry.filename,
        "kind": entry.kind,
        "status": entry.status,
        "bbDocumentId": entry.bbDocumentId,
        "createdAt": entry.createdAt.isoformat(),
    }


async def _search_code(ctx: McpContext, args: dict[str, Any]) -> Any:
    query = _require(args, "query")
    repo = await _require_repo(ctx, args)
    # build_github_toolset raises GitHubError if the org has no installation or
    # the repo is deactivated; the server renders that as an error result.
    _, registry = build_github_toolset(ctx.org, repo, ctx.github)
    return await registry["search_code"]({"query": query})


async def _get_file(ctx: McpContext, args: dict[str, Any]) -> Any:
    path = _require(args, "path")
    repo = await _require_repo(ctx, args)
    _, registry = build_github_toolset(ctx.org, repo, ctx.github)
    return await registry["get_file"]({"path": path, "ref": args.get("ref")})


# ─── context engine ───────────────────────────────────────────────────────────


async def _find_related_context(ctx: McpContext, args: dict[str, Any]) -> Any:
    """Rank the org's prior memories most relevant to a code change."""
    diff = _require(args, "diff")
    repo = await _require_repo(ctx, args)
    max_results = _int_arg(args, "max_results", 20)
    repo_full = f"{repo.owner}/{repo.name}"
    anchors = extract_anchors(diff, repo=repo_full)
    related = await find_related_context(
        anchors,
        bb=ctx.backboard,
        assistant_id=ctx.org.bbAssistantId,
        repo_id=repo.id,
        max_results=max_results,
    )
    return {
        "repo": repo_full,
        "anchors": {"files": anchors.files, "symbols": anchors.symbols},
        "related": [m.model_dump(mode="json") for m in related],
    }


async def _check_consistency(ctx: McpContext, args: dict[str, Any]) -> Any:
    """Judge a code change against the org's prior decisions/memories."""
    diff = _require(args, "diff")
    repo = await _require_repo(ctx, args)
    mode = args.get("mode", "preflight")
    if mode not in ("audit", "preflight"):
        raise McpToolError("mode must be 'audit' or 'preflight'")
    anchors = extract_anchors(diff, repo=f"{repo.owner}/{repo.name}")
    related = await find_related_context(
        anchors,
        bb=ctx.backboard,
        assistant_id=ctx.org.bbAssistantId,
        repo_id=repo.id,
    )
    verdict = await check_consistency(
        diff,
        related,
        mode=mode,
        bb=ctx.backboard,
        assistant_id=ctx.org.bbAssistantId,
        anchors=anchors,
    )
    return verdict.model_dump(mode="json")


# ─── registry ─────────────────────────────────────────────────────────────────

_NO_ARGS = {"type": "object", "properties": {}, "required": []}

MCP_TOOLS: list[McpTool] = [
    McpTool(
        name="list_repos",
        description="List the repositories connected to your organization.",
        input_schema=_NO_ARGS,
        handler=_list_repos,
    ),
    McpTool(
        name="list_members",
        description="List the members of your organization with their roles.",
        input_schema=_NO_ARGS,
        handler=_list_members,
    ),
    McpTool(
        name="list_documents",
        description="List the documents indexed for your organization.",
        input_schema=_NO_ARGS,
        handler=_list_documents,
    ),
    McpTool(
        name="get_document",
        description="Get a single organization document by its id.",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document's id (from list_documents).",
                }
            },
            "required": ["document_id"],
        },
        handler=_get_document,
    ),
    McpTool(
        name="search_code",
        description=(
            "Search code in one of your organization's connected repositories."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo as 'owner/name' or bare 'name'.",
                },
                "query": {
                    "type": "string",
                    "description": "Search terms (GitHub code-search qualifiers ok).",
                },
            },
            "required": ["repo", "query"],
        },
        handler=_search_code,
    ),
    McpTool(
        name="get_file",
        description=(
            "Read a file's contents from one of your organization's connected "
            "repositories."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo as 'owner/name' or bare 'name'.",
                },
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path, e.g. 'app/main.py'.",
                },
                "ref": {
                    "type": "string",
                    "description": "Branch, tag, or SHA. Defaults to the default branch.",
                },
            },
            "required": ["repo", "path"],
        },
        handler=_get_file,
    ),
    McpTool(
        name="find_related_context",
        description=(
            "Given a code change (unified diff) in one of your org's repos, find "
            "the prior memories/decisions most relevant to it — the context the "
            "org already has about this code."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo as 'owner/name' or bare 'name'.",
                },
                "diff": {
                    "type": "string",
                    "description": "Unified diff (git diff or PR diff) of the change.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max memories to return (default 20).",
                },
            },
            "required": ["repo", "diff"],
        },
        handler=_find_related_context,
    ),
    McpTool(
        name="check_consistency",
        description=(
            "Judge whether a code change (unified diff) is consistent with your "
            "org's prior decisions. Returns a verdict (consistent | conflict | "
            "no_prior_context) with any contradicted decisions cited."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo as 'owner/name' or bare 'name'.",
                },
                "diff": {
                    "type": "string",
                    "description": "Unified diff (git diff or PR diff) of the change.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["audit", "preflight"],
                    "description": (
                        "'preflight' for a proposed change, 'audit' for a landed "
                        "one. Default 'preflight'."
                    ),
                },
            },
            "required": ["repo", "diff"],
        },
        handler=_check_consistency,
    ),
]

TOOLS_BY_NAME: dict[str, McpTool] = {t.name: t for t in MCP_TOOLS}
