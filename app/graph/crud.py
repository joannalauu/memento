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
from bson.errors import InvalidId

from app.backboard.models import MemoryIndex
from app.graph import ids
from app.graph.schemas import (
    GraphLink,
    GraphNode,
    GraphNodeMeta,
    GraphPayload,
    NodeDetail,
    NodeType,
    RelatedDecision,
)
from app.orgs.models import User

GRAPH_CACHE_TTL = 60.0  # seconds; new nodes only appear when a PR is distilled

_CacheKey = tuple[str, str | None, str | None, tuple[str, ...] | None]
_cache: dict[_CacheKey, tuple[float, GraphPayload]] = {}
_cache_locks: dict[_CacheKey, asyncio.Lock] = defaultdict(asyncio.Lock)


def _parse_oid(raw: str) -> PydanticObjectId | None:
    """ObjectId or None — bson raises InvalidId (not a ValueError) on bad hex."""
    try:
        return PydanticObjectId(raw)
    except (InvalidId, ValueError, TypeError):
        return None


async def _load_decisions(query: dict[str, object]) -> list[MemoryIndex]:
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
        if u.id is not None
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
    query: dict[str, object] = {"orgId": org_id, "deletedAt": None}
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
            feat_meta = nodes[feat_id].meta
            feat_meta.decisionCount = (feat_meta.decisionCount or 0) + 1
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
            node.val = 2 + log2((node.meta.decisionCount or 0) + 1)
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


async def _related_decisions(
    decisions: list[MemoryIndex],
) -> list[RelatedDecision]:
    """Project decisions into hop targets, newest first, authors resolved in
    one bulk query. Shared by every non-decision node type."""
    names = await _names_by_user_id(
        {d.authorUserId for d in decisions if d.authorUserId}
    )
    ordered = sorted(decisions, key=lambda d: d.createdAt, reverse=True)
    return [
        RelatedDecision(
            id=ids.decision_id(d.id),
            label=ids.short_label(d.contentSnapshot),
            prNumber=d.prNumber,
            author=names.get(d.authorUserId) if d.authorUserId else None,
            date=d.createdAt,
            stalenessStatus=d.stalenessStatus,
        )
        for d in ordered
    ]


async def get_node_detail(org_id: PydanticObjectId, node_id: str) -> NodeDetail | None:
    """Full detail for a single clicked node, scoped to `org_id`.

    Decision nodes return the complete snapshot + provenance; every other node
    type returns the decisions it connects to (`relatedDecisions`) so a click
    becomes a hop. Returns None (→ route 404) for a node that doesn't exist in
    this org, a soft-deleted decision, or an unparseable id.
    """
    base: dict[str, object] = {"orgId": org_id, "deletedAt": None}

    try:
        parsed = ids.parse_node_id(node_id)
    except ValueError:
        return None

    if parsed.type == "decision":
        oid = _parse_oid(parsed.rest)
        if oid is None:
            return None
        d = await MemoryIndex.get(oid)
        if d is None or d.orgId != org_id or d.deletedAt is not None:
            return None
        names = await _names_by_user_id({d.authorUserId} if d.authorUserId else set())
        pr_url = (
            f"https://github.com/{d.anchors.repo}/pull/{d.prNumber}"
            if d.anchors.repo and d.prNumber is not None
            else None
        )
        return NodeDetail(
            id=node_id,
            type="decision",
            label=ids.short_label(d.contentSnapshot),
            contentSnapshot=d.contentSnapshot,
            prNumber=d.prNumber,
            prUrl=pr_url,
            author=names.get(d.authorUserId) if d.authorUserId else None,
            date=d.createdAt,
            feature=d.feature,
            files=d.anchors.files,
            symbols=d.anchors.symbols,
            stalenessStatus=d.stalenessStatus,
            confidence=d.confidence,
            supersededBy=(ids.decision_id(d.supersededBy) if d.supersededBy else None),
        )

    # Non-decision nodes: parse_node_id guarantees repo + rest are non-empty for
    # file/pr/feature, and rest for engineer.
    if parsed.type == "file":
        path = parsed.rest
        decisions = await _load_decisions(
            {**base, "anchors.repo": parsed.repo, "anchors.files": path}
        )
        label = posixpath.basename(path) or path

    elif parsed.type == "pr":
        try:
            pr_number = int(parsed.rest)
        except ValueError:
            return None
        decisions = await _load_decisions(
            {**base, "anchors.repo": parsed.repo, "prNumber": pr_number}
        )
        label = f"PR #{pr_number}"

    elif parsed.type == "engineer":
        author_id = _parse_oid(parsed.rest)
        if author_id is None:
            return None
        decisions = await _load_decisions({**base, "authorUserId": author_id})
        names = await _names_by_user_id({author_id})
        label = names.get(author_id, "unknown")

    else:  # feature — parsed.repo is the embedded org id, rest is the name
        if parsed.repo != str(org_id):
            return None
        decisions = await _load_decisions({**base, "feature": parsed.rest})
        label = parsed.rest

    if not decisions:
        return None
    return NodeDetail(
        id=node_id,
        type=parsed.type,
        label=label,
        relatedDecisions=await _related_decisions(decisions),
    )


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
