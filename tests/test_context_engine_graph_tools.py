"""Graph tools (app/context_engine/graph_tools.py): semantic entry points and
directed local graph walks. Mongo helpers and Backboard are faked following the
conventions in test_context_engine_retrieval.py / test_graph_aggregation.py."""

from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine import graph_tools
from app.context_engine.graph_tools import find_entry_points, walk_graph
from app.graph.ids import decision_id, engineer_id, feature_id, file_id, pr_id
from app.traversal import traversal_channel

ORG_ID = PydanticObjectId()
REPO = "acme/api-server"
REPO_ID = PydanticObjectId()


def make_doc(bb_id="bb", files=(), symbols=(), repo=REPO, content=None, **fields):
    return MemoryIndex.model_construct(
        id=fields.pop("id", PydanticObjectId()),
        orgId=ORG_ID,
        repoId=REPO_ID,
        bbMemoryId=bb_id,
        contentSnapshot=content or f"Decision {bb_id}\nbody...",
        anchors=Anchors(repo=repo, files=list(files), symbols=list(symbols)),
        **fields,
    )


def semantic_result(*hits):
    return {"memories": list(hits), "total_count": len(hits)}


def hit(memory_id, score=None):
    h = {"id": memory_id, "content": f"content of {memory_id}"}
    if score is not None:
        h["score"] = score
    return h


@pytest.fixture
def bb():
    mock = AsyncMock()
    mock.search_memories.return_value = semantic_result()
    return mock


@pytest.fixture
def entry_docs(monkeypatch):
    """Patch the entry-point join; return the mutable doc list it serves and a
    capture of the (org_id, repo_id) it was called with."""
    docs: list[MemoryIndex] = []
    calls: list[tuple] = []

    async def fake_lookup(org_id, repo_id, bb_memory_ids):
        calls.append((org_id, repo_id, list(bb_memory_ids)))
        return [d for d in docs if d.bbMemoryId in bb_memory_ids]

    monkeypatch.setattr(graph_tools, "_entry_lookup", fake_lookup)
    return docs, calls


@pytest.fixture
def walk_env(monkeypatch):
    """Fake _find_decisions off a mutable doc list, capturing every query dict.
    Supports the id/prNumber/anchors/feature/authorUserId/supersededBy filters
    the tools issue. Also fakes name resolution."""
    docs: list[MemoryIndex] = []
    queries: list[dict] = []
    names: dict[PydanticObjectId, str] = {}

    def matches(doc, query):
        for key, want in query.items():
            if key == "deletedAt":
                if getattr(doc, "deletedAt", None) is not None:
                    return False
            elif key == "orgId":
                if doc.orgId != want:
                    return False
            elif key == "_id":
                if doc.id != want:
                    return False
            elif key == "prNumber":
                if doc.prNumber != want:
                    return False
            elif key == "authorUserId":
                if doc.authorUserId != want:
                    return False
            elif key == "feature":
                if doc.feature != want:
                    return False
            elif key == "supersededBy":
                if doc.supersededBy != want:
                    return False
            elif key == "anchors.repo":
                if doc.anchors.repo != want:
                    return False
            elif key == "anchors.files":
                if want not in doc.anchors.files:
                    return False
        return True

    async def fake_find(query):
        queries.append(query)
        return [d for d in docs if matches(d, query)]

    async def fake_names(user_ids):
        return {uid: names[uid] for uid in user_ids if uid in names}

    monkeypatch.setattr(graph_tools, "_find_decisions", fake_find)
    monkeypatch.setattr(graph_tools, "_names_by_user_id", fake_names)
    return docs, queries, names


@pytest.fixture
def subscribe_traversal():
    """Capture traversal events for a session id off the shared channel; the
    subscription is torn down after the test. Each test uses a unique session id
    so its seq counter starts fresh at 0."""
    unsubs = []

    def _subscribe(session_id):
        events = []
        unsubs.append(traversal_channel.subscribe(session_id, events.append))
        return events

    yield _subscribe
    for unsub in unsubs:
        unsub()


# --- find_entry_points -------------------------------------------------------


async def test_entry_points_map_to_decision_nodes_in_rank_order(bb, entry_docs):
    docs, _ = entry_docs
    docs.append(make_doc(bb_id="bb-1", content="First line one\nrest"))
    docs.append(make_doc(bb_id="bb-2", content="First line two\nrest"))
    # hit order is 2 then 1; the join returning them shuffled must not reorder
    bb.search_memories.return_value = semantic_result(
        hit("bb-2", score=0.9), hit("bb-1", score=0.4)
    )

    result = await find_entry_points("auth", bb=bb, assistant_id="a", org_id=ORG_ID)

    by_bb = {d.bbMemoryId: d for d in docs}
    assert [(e.nodeId, e.score) for e in result] == [
        (decision_id(by_bb["bb-2"].id), 0.9),
        (decision_id(by_bb["bb-1"].id), 0.4),
    ]
    assert result[0].type == "decision"
    assert result[0].label == "First line two"
    assert result[0].isCommunityReport is False


async def test_entry_point_missing_score_is_none(bb, entry_docs):
    docs, _ = entry_docs
    docs.append(make_doc(bb_id="bb-1"))
    bb.search_memories.return_value = semantic_result(hit("bb-1"))

    result = await find_entry_points("q", bb=bb, assistant_id="a", org_id=ORG_ID)

    assert result[0].score is None


async def test_entry_point_unindexed_hit_dropped(bb, entry_docs):
    docs, _ = entry_docs
    docs.append(make_doc(bb_id="bb-1"))
    bb.search_memories.return_value = semantic_result(hit("bb-1"), hit("bb-ghost"))

    result = await find_entry_points("q", bb=bb, assistant_id="a", org_id=ORG_ID)

    assert [e.nodeId for e in result] == [decision_id(docs[0].id)]


async def test_entry_point_repo_filter_and_limit(bb, entry_docs):
    _, calls = entry_docs
    bb.search_memories.return_value = semantic_result(hit("bb-1"))

    await find_entry_points(
        "q", bb=bb, assistant_id="a", org_id=ORG_ID, repo_id=REPO_ID, limit=3
    )
    assert calls[-1][0] == ORG_ID and calls[-1][1] == REPO_ID
    bb.search_memories.assert_awaited_once_with("a", "q", limit=3)

    await find_entry_points("q", bb=bb, assistant_id="a", org_id=ORG_ID)
    assert calls[-1][1] is None  # repo_id omitted


async def test_entry_points_backboard_failure_propagates(bb, entry_docs):
    bb.search_memories.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await find_entry_points("q", bb=bb, assistant_id="a", org_id=ORG_ID)


# --- walk_graph: decision origin ---------------------------------------------


async def test_walk_decision_groups_all_edge_kinds(walk_env):
    docs, _, names = walk_env
    author = PydanticObjectId()
    names[author] = "Ada"
    target = make_doc(bb_id="bb-new", content="Newer decision\n...")
    origin = make_doc(
        bb_id="bb-origin",
        files=["app/auth.py"],
        symbols=["issue_token"],
        prNumber=7,
        authorUserId=author,
        feature="login",
        supersededBy=target.id,
        content="Use JWT\n...",
    )
    predecessor = make_doc(bb_id="bb-old", supersededBy=origin.id)
    docs.extend([origin, target, predecessor])

    walk = await walk_graph(decision_id(origin.id), org_id=ORG_ID)

    assert walk.type == "decision"
    assert walk.label == "Use JWT"
    assert walk.contentSnapshot == origin.contentSnapshot
    kinds = set(walk.neighbors)
    assert kinds == {
        "governs",
        "introduced",
        "made",
        "belongs_to",
        "superseded_by",
        "supersedes",
    }
    gov = walk.neighbors["governs"][0]
    assert gov.nodeId == file_id(REPO, "app/auth.py")
    assert gov.symbols == ["issue_token"]
    assert walk.neighbors["introduced"][0].nodeId == pr_id(REPO, 7)
    assert walk.neighbors["made"][0].nodeId == engineer_id(author)
    assert walk.neighbors["made"][0].label == "Ada"
    assert walk.neighbors["belongs_to"][0].nodeId == feature_id(ORG_ID, "login")
    sb = walk.neighbors["superseded_by"][0]
    assert sb.nodeId == decision_id(target.id)
    assert sb.contentSnapshot == target.contentSnapshot
    assert walk.neighbors["supersedes"][0].nodeId == decision_id(predecessor.id)


async def test_walk_direction_superseded_by_vs_supersedes(walk_env):
    docs, _, _ = walk_env
    b = make_doc(bb_id="bb-b")
    a = make_doc(bb_id="bb-a", supersededBy=b.id)
    docs.extend([a, b])

    walk_a = await walk_graph(decision_id(a.id), org_id=ORG_ID)
    assert walk_a.neighbors["superseded_by"][0].nodeId == decision_id(b.id)
    assert "supersedes" not in walk_a.neighbors

    walk_b = await walk_graph(decision_id(b.id), org_id=ORG_ID)
    assert walk_b.neighbors["supersedes"][0].nodeId == decision_id(a.id)
    assert "superseded_by" not in walk_b.neighbors


async def test_walk_edge_kinds_filter_skips_other_queries(walk_env):
    docs, queries, _ = walk_env
    b = make_doc(bb_id="bb-b")
    a = make_doc(bb_id="bb-a", files=["app/x.py"], prNumber=3, supersededBy=b.id)
    predecessor = make_doc(bb_id="bb-old", supersededBy=a.id)
    docs.extend([a, b, predecessor])
    queries.clear()

    walk = await walk_graph(
        decision_id(a.id), org_id=ORG_ID, edge_kinds=frozenset({"supersedes"})
    )

    assert set(walk.neighbors) == {"supersedes"}
    # queries: one to load the origin, one for the supersedes filter — nothing else.
    supersedes_queries = [q for q in queries if "supersededBy" in q]
    assert len(supersedes_queries) == 1


# --- walk_graph: virtual origins ---------------------------------------------


@pytest.mark.parametrize(
    "make_node_id,edge_kind,extra",
    [
        (lambda: file_id(REPO, "app/x.py"), "governs", {"files": ["app/x.py"]}),
        (lambda: pr_id(REPO, 5), "introduced", {"prNumber": 5}),
        (lambda: None, "made", {}),  # engineer id built from author below
        (lambda: feature_id(ORG_ID, "billing"), "belongs_to", {"feature": "billing"}),
    ],
)
async def test_walk_virtual_origins(walk_env, make_node_id, edge_kind, extra):
    docs, queries, _ = walk_env
    author = PydanticObjectId()
    doc = make_doc(bb_id="bb-1", authorUserId=author, **extra)
    docs.append(doc)
    node_id = make_node_id() or engineer_id(author)
    queries.clear()

    walk = await walk_graph(node_id, org_id=ORG_ID)

    assert set(walk.neighbors) == {edge_kind}
    assert walk.neighbors[edge_kind][0].nodeId == decision_id(doc.id)
    assert walk.neighbors[edge_kind][0].contentSnapshot == doc.contentSnapshot
    for q in queries:
        assert q["orgId"] == ORG_ID and q["deletedAt"] is None


async def test_walk_unknown_virtual_node_returns_empty(walk_env):
    walk = await walk_graph(file_id(REPO, "does/not/exist.py"), org_id=ORG_ID)
    assert walk.neighbors == {}
    assert walk.type == "file"


# --- walk_graph: depth 2 -----------------------------------------------------


async def test_walk_depth_two_nests_and_excludes_origin(walk_env):
    docs, _, _ = walk_env
    # dec A governs file X and introduces PR 9; walking file X at depth 2 should
    # reach A, then A's own neighbors (PR 9) — but not backtrack to file X.
    a = make_doc(bb_id="bb-a", files=["app/x.py"], prNumber=9)
    docs.append(a)

    walk = await walk_graph(file_id(REPO, "app/x.py"), org_id=ORG_ID, depth=2)

    neighbor = walk.neighbors["governs"][0]
    assert neighbor.nodeId == decision_id(a.id)
    assert neighbor.neighbors is not None
    assert neighbor.neighbors["introduced"][0].nodeId == pr_id(REPO, 9)
    # the origin file must not reappear in the nested governs set
    assert "governs" not in neighbor.neighbors


async def test_walk_group_cap_sets_truncated(walk_env, monkeypatch):
    docs, _, _ = walk_env
    monkeypatch.setattr(graph_tools, "WALK_GROUP_LIMIT", 2)
    for i in range(5):
        docs.append(make_doc(bb_id=f"bb-{i}", feature="billing"))

    walk = await walk_graph(feature_id(ORG_ID, "billing"), org_id=ORG_ID)

    assert walk.truncated is True
    assert len(walk.neighbors["belongs_to"]) == 2


# --- walk_graph: errors ------------------------------------------------------


@pytest.mark.parametrize("depth", [0, 3])
async def test_walk_bad_depth_raises(walk_env, depth):
    docs, _, _ = walk_env
    a = make_doc(bb_id="bb-a")
    docs.append(a)
    with pytest.raises(ValueError):
        await walk_graph(decision_id(a.id), org_id=ORG_ID, depth=depth)


@pytest.mark.parametrize("node_id", ["bogus", "dec:", "weird:x"])
async def test_walk_malformed_id_raises(walk_env, node_id):
    with pytest.raises(ValueError):
        await walk_graph(node_id, org_id=ORG_ID)


async def test_walk_unknown_decision_raises(walk_env):
    with pytest.raises(ValueError):
        await walk_graph(decision_id(PydanticObjectId()), org_id=ORG_ID)


async def test_walk_feature_foreign_org_raises(walk_env):
    other_org = PydanticObjectId()
    with pytest.raises(ValueError):
        await walk_graph(feature_id(other_org, "billing"), org_id=ORG_ID)


async def test_walk_dangling_superseded_by_dropped(walk_env):
    docs, _, _ = walk_env
    a = make_doc(bb_id="bb-a", supersededBy=PydanticObjectId())  # target absent
    docs.append(a)

    walk = await walk_graph(decision_id(a.id), org_id=ORG_ID)

    assert "superseded_by" not in walk.neighbors


# --- traversal event emission ------------------------------------------------


async def test_find_entry_points_emits_entry_event_per_landed_node(
    bb, entry_docs, subscribe_traversal
):
    docs, _ = entry_docs
    docs.append(make_doc(bb_id="bb-1"))
    docs.append(make_doc(bb_id="bb-2"))
    bb.search_memories.return_value = semantic_result(hit("bb-2"), hit("bb-1"))
    events = subscribe_traversal("sess-entry")

    result = await find_entry_points(
        "auth", bb=bb, assistant_id="a", org_id=ORG_ID, session_id="sess-entry"
    )

    # one entry event per landed node, in the same rank order, edges empty
    assert [(e.kind, e.nodeId, e.edgeKind, e.fromNodeId) for e in events] == [
        ("entry", r.nodeId, None, None) for r in result
    ]
    assert [e.seq for e in events] == [0, 1]
    assert all(e.source == "mcp" and e.sessionId == "sess-entry" for e in events)


async def test_walk_emits_hop_event_per_returned_neighbor(
    walk_env, subscribe_traversal
):
    docs, _, names = walk_env
    author = PydanticObjectId()
    names[author] = "Ada"
    origin = make_doc(
        bb_id="bb-origin", files=["app/auth.py"], prNumber=7, authorUserId=author
    )
    docs.append(origin)
    events = subscribe_traversal("sess-hop")

    walk = await walk_graph(
        decision_id(origin.id), org_id=ORG_ID, session_id="sess-hop", source="web"
    )

    # every hop event is (edgeKind, neighbor) with the origin as fromNodeId, and
    # the set of hops matches exactly the neighbors the walk returned
    emitted = {(e.edgeKind, e.nodeId) for e in events}
    returned = {(kind, n.nodeId) for kind, ns in walk.neighbors.items() for n in ns}
    assert emitted == returned
    assert all(
        e.kind == "hop" and e.fromNodeId == decision_id(origin.id) and e.source == "web"
        for e in events
    )


async def test_entry_then_walk_share_monotonic_session_seq(
    bb, entry_docs, walk_env, subscribe_traversal
):
    # entry_docs patches _entry_lookup; walk_env patches _find_decisions — both
    # active so a single session can enter then walk.
    entry_list, _ = entry_docs
    walk_docs, _, _ = walk_env
    origin = make_doc(bb_id="bb-1", files=["app/auth.py"])
    entry_list.append(origin)
    walk_docs.append(origin)
    bb.search_memories.return_value = semantic_result(hit("bb-1"))
    events = subscribe_traversal("sess-both")

    await find_entry_points(
        "q", bb=bb, assistant_id="a", org_id=ORG_ID, session_id="sess-both"
    )
    await walk_graph(decision_id(origin.id), org_id=ORG_ID, session_id="sess-both")

    # seq is monotonic across the two tool calls: entry (0), then the hop(s)
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].kind == "entry"
    assert events[-1].kind == "hop"


async def test_no_session_id_emits_nothing(
    bb, entry_docs, walk_env, subscribe_traversal
):
    entry_list, _ = entry_docs
    walk_docs, _, _ = walk_env
    origin = make_doc(bb_id="bb-1", files=["app/auth.py"])
    entry_list.append(origin)
    walk_docs.append(origin)
    bb.search_memories.return_value = semantic_result(hit("bb-1"))
    # subscribe to the default source's session key the tools would use if they
    # leaked one; nothing should arrive because no session_id was passed.
    events = subscribe_traversal("mcp")

    await find_entry_points("q", bb=bb, assistant_id="a", org_id=ORG_ID)
    await walk_graph(decision_id(origin.id), org_id=ORG_ID)

    assert events == []
