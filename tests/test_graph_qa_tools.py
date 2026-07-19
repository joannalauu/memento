"""Unit tests for the web Q&A toolset (app/graph/qa_tools.py): definition
shapes, closure context-forwarding (session tag, assistant, org), citation
collection, and the org-level GitHub tool lifting."""

import json
from types import SimpleNamespace

from beanie import PydanticObjectId

import app.graph.qa_tools as qa_tools
from app.context_engine.schemas import EntryPoint, GraphWalk, WalkNeighbor
from app.github.tools import GITHUB_TOOL_DEFINITIONS
from app.graph.qa_tools import (
    GRAPH_TOOL_DEFINITIONS,
    MAX_CITATIONS,
    ORG_GITHUB_TOOL_DEFINITIONS,
    CitationCollector,
    build_qa_toolset,
)
from app.mcp.tools import TOOLS_BY_NAME

ORG_ID = PydanticObjectId()
REPO_ID = PydanticObjectId()


def _org(installation_id=123):
    return SimpleNamespace(
        id=ORG_ID,
        slug="acme",
        bbAssistantId="asst-1",
        githubInstallationId=installation_id,
    )


def _repos():
    return [
        SimpleNamespace(
            id=REPO_ID, owner="acme", name="web", defaultBranch="main", active=True
        ),
        SimpleNamespace(
            id=PydanticObjectId(),
            owner="acme",
            name="api",
            defaultBranch="main",
            active=True,
        ),
    ]


def _toolset(monkeypatch=None, *, org=None, repos=None, session_id="sess-1"):
    return build_qa_toolset(
        bb=SimpleNamespace(),
        gh=SimpleNamespace(),
        org=org or _org(),
        repos=_repos() if repos is None else repos,
        session_id=session_id,
    )


# ─── definitions ──────────────────────────────────────────────────────────────


def test_graph_tool_definitions_wrap_mcp_schemas():
    assert [d["function"]["name"] for d in GRAPH_TOOL_DEFINITIONS] == [
        "find_entry_points",
        "walk_graph",
    ]
    for defn in GRAPH_TOOL_DEFINITIONS:
        name = defn["function"]["name"]
        assert defn["type"] == "function"
        assert defn["function"]["parameters"] == TOOLS_BY_NAME[name].input_schema
        assert defn["function"]["description"] == TOOLS_BY_NAME[name].description


def test_org_github_definitions_add_required_repo_param():
    assert len(ORG_GITHUB_TOOL_DEFINITIONS) == len(GITHUB_TOOL_DEFINITIONS)
    for lifted, original in zip(ORG_GITHUB_TOOL_DEFINITIONS, GITHUB_TOOL_DEFINITIONS):
        params = lifted["function"]["parameters"]
        assert params["required"][0] == "repo"
        assert "repo" in params["properties"]
        # original schema preserved (minus the added repo param)...
        for key, prop in original["function"]["parameters"]["properties"].items():
            assert params["properties"][key] == prop
        # ...and the repo-scoped originals were not mutated by the deepcopy
        assert "repo" not in original["function"]["parameters"]["properties"]


def test_github_tools_omitted_without_installation():
    defs, registry, _ = _toolset(org=_org(installation_id=None))
    assert [d["function"]["name"] for d in defs] == ["find_entry_points", "walk_graph"]
    assert set(registry) == {"find_entry_points", "walk_graph"}


def test_github_tools_included_with_installation():
    defs, registry, _ = _toolset()
    names = [d["function"]["name"] for d in defs]
    assert names[:2] == ["find_entry_points", "walk_graph"]
    assert "get_file" in names and "get_file" in registry


# ─── graph tool closures ──────────────────────────────────────────────────────


async def test_find_entry_points_forwards_session_tag(monkeypatch):
    calls = {}

    async def fake_find(query, **kwargs):
        calls["query"] = query
        calls.update(kwargs)
        return [EntryPoint(nodeId="dec:abc", type="decision", label="use mongo")]

    monkeypatch.setattr(qa_tools, "find_entry_points", fake_find)
    _, registry, citations = _toolset(session_id="sess-42")

    out = await registry["find_entry_points"]({"query": "why mongo", "limit": 3})

    assert calls["query"] == "why mongo"
    assert calls["session_id"] == "sess-42"
    assert calls["source"] == "web"
    assert calls["assistant_id"] == "asst-1"
    assert calls["org_id"] == ORG_ID
    assert calls["repo_id"] is None
    assert calls["limit"] == 3
    assert json.loads(out)[0]["nodeId"] == "dec:abc"
    assert citations.as_list() == [{"nodeId": "dec:abc", "prNumber": None}]


async def test_find_entry_points_resolves_repo_scope(monkeypatch):
    calls = {}

    async def fake_find(query, **kwargs):
        calls.update(kwargs)
        return []

    monkeypatch.setattr(qa_tools, "find_entry_points", fake_find)
    _, registry, _ = _toolset()

    await registry["find_entry_points"]({"query": "q", "repo": "acme/web"})
    assert calls["repo_id"] == REPO_ID

    out = await registry["find_entry_points"]({"query": "q", "repo": "nope"})
    assert out.startswith("Error: repo 'nope' not found")


async def test_find_entry_points_requires_query():
    _, registry, _ = _toolset()
    assert (await registry["find_entry_points"]({})).startswith("Error: missing")


async def test_walk_graph_forwards_and_validates_edge_kinds(monkeypatch):
    calls = {}

    async def fake_walk(node_id, **kwargs):
        calls["node_id"] = node_id
        calls.update(kwargs)
        return GraphWalk(nodeId=node_id, type="decision", label="d")

    monkeypatch.setattr(qa_tools, "walk_graph", fake_walk)
    _, registry, _ = _toolset(session_id="sess-7")

    out = await registry["walk_graph"](
        {"node_id": "dec:abc", "edge_kinds": ["governs"], "depth": 2}
    )
    assert calls == {
        "node_id": "dec:abc",
        "org_id": ORG_ID,
        "edge_kinds": frozenset({"governs"}),
        "depth": 2,
        "session_id": "sess-7",
        "source": "web",
    }
    assert json.loads(out)["nodeId"] == "dec:abc"

    bad = await registry["walk_graph"]({"node_id": "dec:abc", "edge_kinds": ["bogus"]})
    assert bad.startswith("Error: unknown edge kind(s): bogus")
    assert (await registry["walk_graph"]({})).startswith("Error: missing")


async def test_walk_graph_records_citations(monkeypatch):
    walk = GraphWalk(
        nodeId="dec:origin",
        type="decision",
        label="origin",
        neighbors={
            "introduced": [
                WalkNeighbor(nodeId="pr:acme/web:41", type="pr", label="PR #41")
            ],
            "supersedes": [
                WalkNeighbor(
                    nodeId="dec:older", type="decision", label="older", prNumber=7
                )
            ],
            "governs": [
                WalkNeighbor(nodeId="file:acme/web:a.py", type="file", label="a.py")
            ],
        },
    )

    async def fake_walk(node_id, **kwargs):
        return walk

    monkeypatch.setattr(qa_tools, "walk_graph", fake_walk)
    _, registry, citations = _toolset()

    await registry["walk_graph"]({"node_id": "dec:origin"})
    # Origin backfills its prNumber from the introduced PR neighbor; decision
    # neighbors are cited; file neighbors are not.
    assert citations.as_list() == [
        {"nodeId": "dec:origin", "prNumber": 41},
        {"nodeId": "dec:older", "prNumber": 7},
    ]


# ─── CitationCollector ────────────────────────────────────────────────────────


def test_citations_dedupe_and_backfill():
    c = CitationCollector()
    c.add("dec:a")
    c.add("dec:b", 5)
    c.add("dec:a", 9)  # later sighting fills the None slot
    c.add("dec:b", 6)  # first non-None wins
    assert c.as_list() == [
        {"nodeId": "dec:a", "prNumber": 9},
        {"nodeId": "dec:b", "prNumber": 5},
    ]


def test_citations_parse_pr_node_ids():
    c = CitationCollector()
    c.add("pr:acme/web:123")
    c.add("not-a-node-id")
    assert c.as_list() == [
        {"nodeId": "pr:acme/web:123", "prNumber": 123},
        {"nodeId": "not-a-node-id", "prNumber": None},
    ]


def test_citations_capped():
    c = CitationCollector()
    for i in range(MAX_CITATIONS + 5):
        c.add(f"dec:{i}")
    assert len(c.as_list()) == MAX_CITATIONS
    assert c.as_list()[0] == {"nodeId": "dec:0", "prNumber": None}


# ─── org-level GitHub tools ───────────────────────────────────────────────────


async def test_github_tools_pop_repo_and_delegate(monkeypatch):
    seen = []
    builds = []

    def fake_build(org, repo, gh):
        builds.append((repo.owner, repo.name))

        async def get_file(args):
            seen.append(args)
            return f"contents from {repo.name}"

        return [], {"get_file": get_file}

    monkeypatch.setattr(qa_tools, "build_github_toolset", fake_build)
    _, registry, _ = _toolset()

    args = {"repo": "acme/web", "path": "a.py"}
    out = await registry["get_file"](args)
    assert out == "contents from web"
    assert seen == [{"path": "a.py"}]  # repo popped before delegating
    assert args == {"repo": "acme/web", "path": "a.py"}  # caller's dict untouched

    # bare-name resolution + per-repo registry cache: one build for two calls
    await registry["get_file"]({"repo": "web", "path": "b.py"})
    assert builds == [("acme", "web")]


async def test_github_tools_unknown_or_missing_repo(monkeypatch):
    monkeypatch.setattr(
        qa_tools,
        "build_github_toolset",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not build")),
    )
    _, registry, _ = _toolset()

    out = await registry["get_file"]({"repo": "ghost", "path": "a.py"})
    assert out.startswith("Error: repo 'ghost' not found in this org")
    assert "acme/web" in out  # lists what IS available

    missing = await registry["get_file"]({"path": "a.py"})
    assert missing.startswith("Error: missing required argument 'repo'")
