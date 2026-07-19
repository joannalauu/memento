"""Toolset for the web graph Q&A endpoint (T4.5, app/graph/ask.py).

Adapts the graph-traversal primitives (app/context_engine/graph_tools.py) and
the per-repo GitHub toolset (app/github/tools.py) into one (tool_definitions,
registry) pair for the Backboard executor loop, bound to a single ask:

- Graph tools forward the ask's session tag (``session_id`` + ``source="web"``)
  so their traversal emissions route to this ask's live view, and record every
  node the model actually touched into a `CitationCollector` — the source of
  the final ``done`` frame's citations.
- GitHub tools are lifted from repo-scoped to org-scoped by adding a required
  ``repo`` argument, resolved against the org's connected repos. An org with no
  GitHub App installation simply gets no GitHub tools (the ask still works).

Tool functions follow the executor's contract (dict args -> str): expected,
model-fixable failures return "Error: ..." strings; anything raised is rendered
as an error output by the executor (executor.py `_run_one`).
"""

import copy
import json
from typing import Any, get_args

from app.backboard.client import Backboard
from app.backboard.executor import ToolFn
from app.context_engine import EntryPoint, GraphWalk, WalkEdgeKind
from app.context_engine import find_entry_points, walk_graph
from app.github.client import GitHubApp
from app.github.tools import GITHUB_TOOL_DEFINITIONS, build_github_toolset
from app.graph.ids import parse_node_id
from app.mcp.tools import TOOLS_BY_NAME
from app.orgs.models import Org, Repo

_WALK_EDGE_KINDS: frozenset[str] = frozenset(get_args(WalkEdgeKind))

# Citation cap for the done frame — a deep walk can touch far more nodes than a
# reader will ever chase; first-seen order keeps the entry points on top.
MAX_CITATIONS = 20


def _to_openai(name: str) -> dict[str, Any]:
    """Wrap an MCP tool's schema into the OpenAI-function format Backboard
    consumes — the MCP registry (app/mcp/tools.py) stays the single source of
    truth for the graph tools' name/description/parameters."""
    tool = TOOLS_BY_NAME[name]
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


GRAPH_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _to_openai("find_entry_points"),
    _to_openai("walk_graph"),
]


def _with_repo_param(defn: dict[str, Any]) -> dict[str, Any]:
    """A repo-scoped GitHub tool definition, lifted to org scope: same schema
    plus a required ``repo`` argument."""
    lifted = copy.deepcopy(defn)
    params = lifted["function"]["parameters"]
    params["properties"] = {
        "repo": {
            "type": "string",
            "description": "Repository as 'owner/name' or bare 'name'.",
        },
        **params["properties"],
    }
    params["required"] = ["repo", *params.get("required", [])]
    return lifted


ORG_GITHUB_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _with_repo_param(d) for d in GITHUB_TOOL_DEFINITIONS
]


def _repo_from_ref(repos: list[Repo], ref: str) -> Repo | None:
    """Resolve 'owner/name' or bare 'name' against the org's connected repos —
    same semantics as the MCP layer's `_resolve_repo`, but against the repo
    list the ask already loaded (no extra query per tool call)."""
    if "/" in ref:
        owner, name = ref.split("/", 1)
        return next((r for r in repos if r.owner == owner and r.name == name), None)
    return next((r for r in repos if r.name == ref), None)


def _pr_number_of(node_id: str, explicit: int | None) -> int | None:
    """A citation's prNumber: the neighbor's own field when set, else parsed
    out of a ``pr:owner/name:123`` node id, else None."""
    if explicit is not None:
        return explicit
    try:
        parsed = parse_node_id(node_id)
    except ValueError:
        return None
    if parsed.type != "pr":
        return None
    try:
        return int(parsed.rest)
    except ValueError:
        return None


class CitationCollector:
    """Accumulates {nodeId, prNumber} citations across a single ask's tool
    calls. Dedupes by nodeId, preserving first-seen order; a later sighting
    that carries a prNumber fills a previously-None slot."""

    def __init__(self) -> None:
        self._citations: dict[str, int | None] = {}

    def add(self, node_id: str, pr_number: int | None = None) -> None:
        resolved = _pr_number_of(node_id, pr_number)
        if node_id in self._citations:
            if self._citations[node_id] is None and resolved is not None:
                self._citations[node_id] = resolved
        else:
            self._citations[node_id] = resolved

    def add_entries(self, entries: list[EntryPoint]) -> None:
        for entry in entries:
            self.add(entry.nodeId)

    def add_walk(self, walk: GraphWalk) -> None:
        """Cite the walk origin and its first-hop decision neighbors. File/
        engineer/feature neighbors and depth-2 fan-out are navigation, not
        evidence — citing them would drown the done frame."""
        origin_pr = next(
            (
                _pr_number_of(n.nodeId, n.prNumber)
                for n in walk.neighbors.get("introduced", [])
                if n.type == "pr"
            ),
            None,
        )
        self.add(walk.nodeId, origin_pr)
        for group in walk.neighbors.values():
            for neighbor in group:
                if neighbor.type == "decision":
                    self.add(neighbor.nodeId, neighbor.prNumber)

    def as_list(self) -> list[dict[str, Any]]:
        return [
            {"nodeId": node_id, "prNumber": pr_number}
            for node_id, pr_number in list(self._citations.items())[:MAX_CITATIONS]
        ]


def _build_graph_registry(
    *,
    bb: Backboard,
    org: Org,
    repos: list[Repo],
    session_id: str,
    citations: CitationCollector,
) -> dict[str, ToolFn]:
    async def _find_entry_points(args: dict[str, Any]) -> str:
        query = args.get("query")
        if not query:
            return "Error: missing required argument 'query'"
        repo_id = None
        ref = args.get("repo")
        if ref:
            repo = _repo_from_ref(repos, ref)
            if repo is None:
                return f"Error: repo {ref!r} not found in this org"
            repo_id = repo.id
        entries = await find_entry_points(
            query,
            bb=bb,
            assistant_id=org.bbAssistantId,
            org_id=org.id,
            repo_id=repo_id,
            limit=int(args.get("limit", 5)),
            session_id=session_id,
            source="web",
        )
        citations.add_entries(entries)
        return json.dumps([e.model_dump(mode="json") for e in entries])

    async def _walk_graph(args: dict[str, Any]) -> str:
        node_id = args.get("node_id")
        if not node_id:
            return "Error: missing required argument 'node_id'"
        edge_kinds: frozenset[WalkEdgeKind] | None = None
        raw_kinds = args.get("edge_kinds")
        if raw_kinds:
            if not isinstance(raw_kinds, list) or not all(
                isinstance(k, str) for k in raw_kinds
            ):
                return "Error: edge_kinds must be a list of strings"
            unknown = set(raw_kinds) - _WALK_EDGE_KINDS
            if unknown:
                return (
                    f"Error: unknown edge kind(s): {', '.join(sorted(unknown))}; "
                    f"valid kinds: {', '.join(sorted(_WALK_EDGE_KINDS))}"
                )
            edge_kinds = frozenset(raw_kinds)  # type: ignore[arg-type]
        # ValueError (malformed node id, bad depth) propagates — the executor
        # renders it as an error output the model can react to.
        walk = await walk_graph(
            node_id,
            org_id=org.id,
            edge_kinds=edge_kinds,
            depth=int(args.get("depth", 1)),
            session_id=session_id,
            source="web",
        )
        citations.add_walk(walk)
        return walk.model_dump_json()

    return {"find_entry_points": _find_entry_points, "walk_graph": _walk_graph}


def _build_org_github_registry(
    org: Org, repos: list[Repo], gh: GitHubApp
) -> dict[str, ToolFn]:
    # One underlying repo-scoped registry per repo actually used, built lazily —
    # build_github_toolset enforces repo.active/installation (GitHubError there
    # becomes an executor error output).
    cache: dict[tuple[str, str], dict[str, ToolFn]] = {}

    def _registry_for(repo: Repo) -> dict[str, ToolFn]:
        key = (repo.owner, repo.name)
        if key not in cache:
            _, registry = build_github_toolset(org, repo, gh)
            cache[key] = registry
        return cache[key]

    def _wrap(name: str) -> ToolFn:
        async def fn(args: dict[str, Any]) -> str:
            args = dict(args)  # never mutate the executor's dict
            ref = args.pop("repo", None)
            if not ref:
                return "Error: missing required argument 'repo'"
            repo = _repo_from_ref(repos, ref)
            if repo is None:
                available = ", ".join(f"{r.owner}/{r.name}" for r in repos) or "(none)"
                return (
                    f"Error: repo {ref!r} not found in this org. Available: {available}"
                )
            return await _registry_for(repo)[name](args)

        return fn

    return {
        d["function"]["name"]: _wrap(d["function"]["name"])
        for d in GITHUB_TOOL_DEFINITIONS
    }


def build_qa_toolset(
    *,
    bb: Backboard,
    gh: GitHubApp,
    org: Org,
    repos: list[Repo],
    session_id: str,
) -> tuple[list[dict[str, Any]], dict[str, ToolFn], CitationCollector]:
    """Bind the Q&A tool suite to one ask. Returns (tool_definitions, registry,
    citations) — the pair for app/backboard/executor.py plus the collector the
    transport reads after the run for the done frame."""
    citations = CitationCollector()
    definitions = list(GRAPH_TOOL_DEFINITIONS)
    registry = _build_graph_registry(
        bb=bb, org=org, repos=repos, session_id=session_id, citations=citations
    )
    if org.githubInstallationId is not None:
        definitions += ORG_GITHUB_TOOL_DEFINITIONS
        registry.update(_build_org_github_registry(org, repos, gh))
    return definitions, registry, citations
