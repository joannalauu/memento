"""Graph aggregation (app/graph/crud.py): projection of memoryIndex into
the {nodes, links} GraphPayload, node dedup via deterministic ids, degree
sizing, scope filters, and the ~60s TTL cache."""

from math import log2
from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.backboard.models import Anchors, MemoryIndex
from app.dependencies import get_current_user
from app.graph import crud
from app.graph import routes as graph_routes
from app.graph.crud import build_graph, get_graph_cached
from app.main import app

ORG_ID = PydanticObjectId()
REPO_ID = PydanticObjectId()
REPO = "acme/api-server"


def make_decision(files=(), symbols=(), repo=REPO, **fields):
    return MemoryIndex.model_construct(
        id=PydanticObjectId(),
        orgId=ORG_ID,
        repoId=REPO_ID,
        bbMemoryId=str(PydanticObjectId()),
        contentSnapshot=fields.pop("content", "Use JWT for session auth\nbecause..."),
        anchors=Anchors(repo=repo, files=list(files), symbols=list(symbols)),
        **fields,
    )


@pytest.fixture
def graph_env(monkeypatch):
    """Patch the module-level Mongo helpers; return (decisions, names, queries)
    where `queries` captures every filter dict passed to the decision load."""
    decisions: list[MemoryIndex] = []
    names: dict[PydanticObjectId, str] = {}
    queries: list[dict] = []

    async def fake_load(query):
        queries.append(query)
        return list(decisions)

    async def fake_names(user_ids):
        return {uid: names[uid] for uid in user_ids if uid in names}

    monkeypatch.setattr(crud, "_load_decisions", fake_load)
    monkeypatch.setattr(crud, "_names_by_user_id", fake_names)
    monkeypatch.setattr(crud, "_cache", {})
    monkeypatch.setattr(crud, "_cache_locks", crud.defaultdict(crud.asyncio.Lock))
    return decisions, names, queries


def nodes_by_id(payload):
    return {n.id: n for n in payload.nodes}


async def test_shared_file_dedups_and_governs_carries_symbols(graph_env):
    decisions, names, _ = graph_env
    author = PydanticObjectId()
    names[author] = "Ada"
    decisions.append(
        make_decision(
            files=["app/auth.py"],
            symbols=["issue_token"],
            prNumber=7,
            authorUserId=author,
            feature="auth",
            stalenessStatus="gap",
            confidence="verified",
        )
    )
    decisions.append(make_decision(files=["app/auth.py"]))

    payload = await build_graph(ORG_ID)
    nodes = nodes_by_id(payload)

    # Two decisions anchoring the same repo-qualified path share ONE file node.
    file_nodes = [n for n in payload.nodes if n.type == "file"]
    assert len(file_nodes) == 1
    assert file_nodes[0].id == f"file:{REPO}:app/auth.py"
    assert file_nodes[0].label == "auth.py"
    assert file_nodes[0].meta.path == "app/auth.py"

    governs = [link for link in payload.links if link.kind == "governs"]
    assert len(governs) == 2
    assert governs[0].symbols == ["issue_token"]  # symbols ride the edge

    d1 = nodes[f"dec:{decisions[0].id}"]
    assert d1.label == "Use JWT for session auth"
    assert d1.meta.stalenessStatus == "gap"  # cached field passed through
    assert d1.meta.confidence == "verified"
    assert d1.meta.author == "Ada"
    assert nodes[f"pr:{REPO}:7"].label == "PR #7"
    assert nodes[f"eng:{author}"].label == "Ada"
    feat = nodes[f"feat:{ORG_ID}:auth"]
    assert feat.meta.decisionCount == 1
    kinds = {link.kind for link in payload.links}
    assert kinds == {"governs", "introduced", "made", "belongs_to"}


async def test_file_and_pr_ids_are_repo_qualified(graph_env):
    decisions, _, _ = graph_env
    decisions.append(make_decision(files=["src/index.ts"], prNumber=1, repo="acme/api"))
    decisions.append(make_decision(files=["src/index.ts"], prNumber=1, repo="acme/web"))

    payload = await build_graph(ORG_ID)
    file_ids = {n.id for n in payload.nodes if n.type == "file"}
    pr_ids = {n.id for n in payload.nodes if n.type == "pr"}
    assert file_ids == {"file:acme/api:src/index.ts", "file:acme/web:src/index.ts"}
    assert pr_ids == {"pr:acme/api:1", "pr:acme/web:1"}


async def test_val_sizing_decision_clamped_feature_by_count(graph_env):
    decisions, _, _ = graph_env
    decisions.append(make_decision(files=[f"f{i}.py" for i in range(20)]))
    for _ in range(3):
        decisions.append(make_decision(feature="billing"))

    payload = await build_graph(ORG_ID)
    nodes = nodes_by_id(payload)

    big = nodes[f"dec:{decisions[0].id}"]
    assert big.val == 4  # min(4, 1 + log2(21)) clamps
    small = nodes[f"dec:{decisions[1].id}"]
    assert small.val == 1 + log2(2)  # degree 1 (belongs_to only)
    feat = nodes[f"feat:{ORG_ID}:billing"]
    assert feat.meta.decisionCount == 3
    assert feat.val == 2 + log2(4)
    one_file = nodes["file:acme/api-server:f0.py"]
    assert one_file.val == 1 + log2(2)


async def test_superseded_by_kept_in_scope_dropped_when_dangling(graph_env):
    decisions, _, _ = graph_env
    current = make_decision()
    old = make_decision(supersededBy=current.id)
    dangling = make_decision(supersededBy=PydanticObjectId())  # target not loaded
    decisions.extend([current, old, dangling])

    payload = await build_graph(ORG_ID)
    superseded = [link for link in payload.links if link.kind == "superseded_by"]
    assert len(superseded) == 1
    assert superseded[0].source == f"dec:{old.id}"
    assert superseded[0].target == f"dec:{current.id}"
    # Semantic link, not a size signal: no degree bump on either end.
    assert nodes_by_id(payload)[f"dec:{current.id}"].val == 1 + log2(2)


async def test_types_filter_drops_nodes_and_their_links(graph_env):
    decisions, _, _ = graph_env
    decisions.append(make_decision(files=["a.py"], prNumber=2, feature="auth"))

    payload = await build_graph(ORG_ID, types=frozenset({"decision", "feature"}))
    assert {n.type for n in payload.nodes} == {"decision", "feature"}
    assert {link.kind for link in payload.links} == {"belongs_to"}


async def test_repo_and_feature_filters_apply_at_the_query(graph_env):
    _, _, queries = graph_env
    await build_graph(ORG_ID, repo=REPO, feature="auth")
    assert queries == [
        {
            "orgId": ORG_ID,
            "deletedAt": None,
            "anchors.repo": REPO,
            "feature": "auth",
        }
    ]


async def test_cache_hits_within_ttl_and_keys_by_scope(graph_env):
    decisions, _, queries = graph_env
    decisions.append(make_decision(files=["a.py"]))

    first = await get_graph_cached(ORG_ID)
    second = await get_graph_cached(ORG_ID)
    assert second is first  # served from cache — no rebuild
    assert len(queries) == 1

    await get_graph_cached(ORG_ID, repo=REPO)  # different scope, own entry
    assert len(queries) == 2

    key = (str(ORG_ID), None, None, None)
    expired_at, payload = crud._cache[key]
    crud._cache[key] = (crud.time.monotonic() - 1, payload)  # force expiry
    await get_graph_cached(ORG_ID)
    assert len(queries) == 3  # rebuilt after TTL


async def test_expired_scopes_and_their_locks_are_evicted(graph_env):
    # Regression: distinct filter scopes used to accumulate in _cache/_cache_locks
    # forever (repo/feature/types are client-supplied) — a memory leak. Expired
    # entries and their orphaned locks must be reclaimed, not just overwritten.
    decisions, _, _ = graph_env
    decisions.append(make_decision(files=["a.py"]))

    for i in range(5):
        await get_graph_cached(ORG_ID, feature=f"feat-{i}")
    assert len(crud._cache) == 5
    assert len(crud._cache_locks) == 5  # one lock per scope

    # Age every entry past the TTL, then make one fresh request (the slow path,
    # which is what triggers the prune).
    for k, (_, payload) in list(crud._cache.items()):
        crud._cache[k] = (crud.time.monotonic() - 1, payload)
    await get_graph_cached(ORG_ID, feature="new")

    # Only the just-built scope survives; the 5 dead scopes and their locks are
    # gone rather than lingering for the life of the process.
    assert list(crud._cache) == [(str(ORG_ID), None, "new", None)]
    assert set(crud._cache_locks) <= set(crud._cache)


async def test_cache_size_is_capped(graph_env, monkeypatch):
    # Even within the TTL window, a burst of distinct scopes can't grow the cache
    # without bound — it's capped, evicting the entries closest to expiry.
    decisions, _, queries = graph_env
    decisions.append(make_decision(files=["a.py"]))
    monkeypatch.setattr(crud, "GRAPH_CACHE_MAX_ENTRIES", 3)

    for i in range(10):
        await get_graph_cached(ORG_ID, feature=f"f{i}")

    assert len(queries) == 10  # every distinct scope was built
    # Prune caps to MAX before each insert, so steady state is at most MAX + 1.
    assert len(crud._cache) <= crud.GRAPH_CACHE_MAX_ENTRIES + 1
    assert set(crud._cache_locks) <= set(crud._cache)


# --- route: GET /orgs/{org_id}/graph ---

MEMBER_ID = PydanticObjectId()


@pytest.fixture
def api(graph_env, monkeypatch):
    """ASGI client authed as MEMBER_ID, with the org lookup faked. Returns
    (client, org_holder) — set org_holder['org'] to control get_org."""
    holder = {"org": SimpleNamespace(members=[SimpleNamespace(userId=MEMBER_ID)])}

    async def fake_get_org(org_id):
        return holder["org"]

    monkeypatch.setattr(graph_routes, "get_org", fake_get_org)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=MEMBER_ID)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield client, holder
    finally:
        app.dependency_overrides.clear()


async def test_route_returns_payload_with_types_filter(api, graph_env):
    client, _ = api
    decisions, _, _ = graph_env
    decisions.append(make_decision(files=["a.py"], feature="auth"))

    async with client:
        resp = await client.get(
            f"/orgs/{ORG_ID}/graph", params={"types": "decision,feature"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert {n["type"] for n in body["nodes"]} == {"decision", "feature"}
    assert [link["kind"] for link in body["links"]] == ["belongs_to"]


async def test_route_404_unknown_org(api):
    client, holder = api
    holder["org"] = None
    async with client:
        resp = await client.get(f"/orgs/{ORG_ID}/graph")
    assert resp.status_code == 404


async def test_route_403_non_member(api):
    client, holder = api
    holder["org"] = SimpleNamespace(
        members=[SimpleNamespace(userId=PydanticObjectId())]
    )
    async with client:
        resp = await client.get(f"/orgs/{ORG_ID}/graph")
    assert resp.status_code == 403


async def test_route_400_unknown_type(api):
    client, _ = api
    async with client:
        resp = await client.get(f"/orgs/{ORG_ID}/graph", params={"types": "bogus"})
    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]
