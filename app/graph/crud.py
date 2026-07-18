"""Graph aggregation: project memoryIndex's implicit graph into GraphPayload.

Pure read. Deterministic node ids are the dedup key — building nodes into a
dict keyed by id collapses duplicates, and that shared-junction collapse is
what *creates* connectivity (two PRs touching the same file connect only
because both emit an edge to the identical `file:...` id):

    decision  ->  "dec:<memoryIndex._id>"
    file      ->  "file:<repo>:<path>"      (repo-qualified — org spans repos)
    pr        ->  "pr:<repo>:<prNumber>"
    engineer  ->  "eng:<userId>"
    feature   ->  "feat:<orgId>:<featureName>"

Staleness is READ from the cached `MemoryIndex.stalenessStatus` stamped by the
background sweep (app/context_engine/staleness_sweep.py). Never call
`staleness_check` here — it hits GitHub per anchored file, and doing that for
every node on every graph load would make the endpoint unusable.
"""

import asyncio
import posixpath
import time
from collections import defaultdict
from math import log2

from beanie import PydanticObjectId

from app.backboard.models import MemoryIndex
from app.graph import ids
from app.graph.schemas import (
    GraphLink,
    GraphNode,
    GraphNodeMeta,
    GraphPayload,
    NodeType,
)
from app.orgs.models import User

GRAPH_CACHE_TTL = 60.0  # seconds; new nodes only appear when a PR is distilled

_CacheKey = tuple[str, str | None, str | None, tuple[str, ...] | None]
_cache: dict[_CacheKey, tuple[float, GraphPayload]] = {}
_cache_locks: dict[_CacheKey, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _load_decisions(query: dict) -> list[MemoryIndex]:
    return await MemoryIndex.find(query).to_list()


async def _names_by_user_id(
    user_ids: set[PydanticObjectId],
) -> dict[PydanticObjectId, str]:
    """Resolve author ids -> display names in one bulk query."""
    if not user_ids:
        return {}
    users = await User.find({"_id": {"$in": list(user_ids)}}).to_list()
    return {
        u.id: (u.name or u.githubUsername or u.email or "unknown")
        for u in users
        if u.id
    }


async def build_graph(
    org_id: PydanticObjectId,
    *,
    repo: str | None = None,
    feature: str | None = None,
    types: frozenset[NodeType] | None = None,
) -> GraphPayload:
    """Assemble the org's knowledge graph, scoped by the given filters.

    Filters apply at the query (not post-hoc) so a scoped view stays small;
    `types` optionally restricts which node types survive (links whose
    endpoints are dropped go with them).
    """
    query: dict = {"orgId": org_id, "deletedAt": None}
    if repo:
        query["anchors.repo"] = repo
    if feature:
        query["feature"] = feature
    decisions = await _load_decisions(query)

    author_ids = {d.authorUserId for d in decisions if d.authorUserId}
    names = await _names_by_user_id(author_ids)

    nodes: dict[str, GraphNode] = {}  # keyed by node id — the dedup mechanism
    links: list[GraphLink] = []
    degree: dict[str, int] = defaultdict(int)

    def ensure(node: GraphNode) -> None:
        if node.id not in nodes:
            nodes[node.id] = node

    for d in decisions:
        repo_name = d.anchors.repo
        dec_id = ids.decision_id(d.id)
        ensure(
            GraphNode(
                id=dec_id,
                type="decision",
                label=ids.short_label(d.contentSnapshot),
                val=1,
                meta=GraphNodeMeta(
                    prNumber=d.prNumber,
                    author=names.get(d.authorUserId) if d.authorUserId else None,
                    date=d.createdAt,
                    # Cached field written by the staleness sweep; read-only
                    # here. None = not yet swept, renders unflagged.
                    stalenessStatus=d.stalenessStatus,
                    confidence=d.confidence,
                ),
            )
        )

        # governs: one edge per anchored file, symbols folded onto the edge
        for path in d.anchors.files:
            file_id = ids.file_id(repo_name, path)
            ensure(
                GraphNode(
                    id=file_id,
                    type="file",
                    label=posixpath.basename(path) or path,
                    val=1,
                    meta=GraphNodeMeta(path=path),
                )
            )
            links.append(
                GraphLink(
                    source=dec_id,
                    target=file_id,
                    kind="governs",
                    symbols=d.anchors.symbols,
                )
            )
            degree[dec_id] += 1
            degree[file_id] += 1

        # introduced
        if d.prNumber is not None:
            pr_id = ids.pr_id(repo_name, d.prNumber)
            ensure(
                GraphNode(
                    id=pr_id,
                    type="pr",
                    label=f"PR #{d.prNumber}",
                    val=1,
                    meta=GraphNodeMeta(prNumber=d.prNumber),
                )
            )
            links.append(GraphLink(source=pr_id, target=dec_id, kind="introduced"))
            degree[pr_id] += 1
            degree[dec_id] += 1

        # made
        if d.authorUserId:
            eng_id = ids.engineer_id(d.authorUserId)
            ensure(
                GraphNode(
                    id=eng_id,
                    type="engineer",
                    label=names.get(d.authorUserId, "unknown"),
                    val=1,
                )
            )
            links.append(GraphLink(source=eng_id, target=dec_id, kind="made"))
            degree[eng_id] += 1
            degree[dec_id] += 1

        # belongs_to
        if d.feature:
            feat_id = ids.feature_id(org_id, d.feature)
            ensure(
                GraphNode(
                    id=feat_id,
                    type="feature",
                    label=d.feature,
                    val=1,
                    meta=GraphNodeMeta(decisionCount=0),
                )
            )
            nodes[feat_id].meta.decisionCount += 1
            links.append(GraphLink(source=dec_id, target=feat_id, kind="belongs_to"))
            degree[feat_id] += 1
            degree[dec_id] += 1

        # superseded_by — semantic link, not a size signal (no degree bump);
        # dropped below if the target decision fell outside the scope
        if d.supersededBy:
            links.append(
                GraphLink(
                    source=dec_id,
                    target=ids.decision_id(d.supersededBy),
                    kind="superseded_by",
                )
            )

    # Size by degree: decisions clamped so one giant decision doesn't dwarf
    # all; features scale with decisionCount so big features read as hubs.
    for node in nodes.values():
        deg = degree[node.id] or 1
        if node.type == "decision":
            node.val = min(4, 1 + log2(deg + 1))
        elif node.type == "feature":
            node.val = 2 + log2(node.meta.decisionCount + 1)
        else:
            node.val = 1 + log2(deg + 1)

    if types is not None:
        nodes = {nid: n for nid, n in nodes.items() if n.type in types}

    # Drop links with a missing endpoint: out-of-scope superseded_by targets
    # and edges into nodes removed by the types filter.
    present = set(nodes)
    links = [
        link for link in links if link.source in present and link.target in present
    ]

    return GraphPayload(nodes=list(nodes.values()), links=links)


async def get_graph_cached(
    org_id: PydanticObjectId,
    *,
    repo: str | None = None,
    feature: str | None = None,
    types: frozenset[NodeType] | None = None,
) -> GraphPayload:
    """`build_graph` behind a ~60s in-memory TTL cache, one entry per
    (org, repo, feature, types) scope, with a per-key lock so concurrent
    requests don't stampede the build."""
    key: _CacheKey = (
        str(org_id),
        repo,
        feature,
        tuple(sorted(types)) if types is not None else None,
    )
    cached = _cache.get(key)
    if cached and time.monotonic() < cached[0]:
        return cached[1]
    async with _cache_locks[key]:
        cached = _cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        payload = await build_graph(org_id, repo=repo, feature=feature, types=types)
        _cache[key] = (time.monotonic() + GRAPH_CACHE_TTL, payload)
        return payload
