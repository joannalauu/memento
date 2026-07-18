"""Graph traversal primitives: semantic entry, then directed local walks.

Two composable tools that let an agent navigate the knowledge graph the way the
graph is meant to be read — enter on a semantically-relevant node, then follow
specific edges rather than exploding all neighbors:

    find_entry_points(query)  Backboard search -> graph node ids to start on
    walk_graph(nodeId)        one node's neighbors, grouped by edge kind

Both read directly from `memoryIndex` rather than the cached `build_graph`
projection: a walk must return `contentSnapshot` (which the render-oriented
`GraphNode` omits), and the dominant caller is an agent that may have *just*
written or superseded a memory — a 60s-stale adjacency would miss the exact
edge it created. Node-id construction/parsing is shared with the projection via
`app.graph.ids`. Read-only: writes nothing.

Imported by MCP (T5.2), web Q&A (T4.5), and community-report routing (T4b) —
kept as composable primitives, not baked into any one caller, because each
feature picks its traversal strategy per question.
"""

import asyncio
import posixpath
from typing import Any

from beanie import PydanticObjectId

from app.backboard.client import Backboard
from app.backboard.models import MemoryIndex
from app.context_engine.schemas import EntryPoint, GraphWalk, WalkEdgeKind, WalkNeighbor
from app.graph.ids import (
    decision_id,
    engineer_id,
    feature_id,
    file_id,
    parse_node_id,
    pr_id,
    short_label,
)
from app.orgs.models import User
from app.traversal import Source, TraversalTag, traversal_channel

ENTRY_POINT_LIMIT = 5
WALK_MAX_DEPTH = 2  # hard cap — multi-hop subgraphs balloon agent context
WALK_GROUP_LIMIT = 25  # per-edge-kind neighbor cap; hub files/features can be huge


# --- Mongo seams (module-level so tests can monkeypatch them) ----------------


async def _find_decisions(query: dict) -> list[MemoryIndex]:
    """The single decision loader; every query is org-scoped and active-only."""
    return await MemoryIndex.find(query).to_list()


async def _entry_lookup(
    org_id: PydanticObjectId,
    repo_id: PydanticObjectId | None,
    bb_memory_ids: list[str],
) -> list[MemoryIndex]:
    """Join semantic hits back to their index docs (repo-scoped when asked)."""
    query: dict = {
        "bbMemoryId": {"$in": bb_memory_ids},
        "orgId": org_id,
        "deletedAt": None,
    }
    if repo_id is not None:
        query["repoId"] = repo_id
    return await MemoryIndex.find(query).to_list()


async def _names_by_user_id(
    user_ids: set[PydanticObjectId],
) -> dict[PydanticObjectId, str]:
    """Resolve author ids -> display names in one bulk query."""
    if not user_ids:
        return {}
    users = await User.find({"_id": {"$in": list(user_ids)}}).to_list()
    # `if u.id` never filters at runtime (loaded docs always have an id) but
    # narrows Beanie's `id: PydanticObjectId | None` to the non-None key type.
    return {
        u.id: (u.name or u.githubUsername or u.email or "unknown")
        for u in users
        if u.id
    }


# --- find_entry_points -------------------------------------------------------


def _hit_id(hit: dict[str, Any]) -> str | None:
    raw = hit.get("id") or hit.get("memory_id")
    return str(raw) if raw else None


def _hit_score(hit: dict[str, Any]) -> float | None:
    raw = hit.get("score")
    # bool is an int subclass; a stray True must not become a 1.0 score
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


async def find_entry_points(
    query: str,
    *,
    bb: Backboard,
    assistant_id: str,
    org_id: PydanticObjectId,
    repo_id: PydanticObjectId | None = None,
    limit: int = ENTRY_POINT_LIMIT,
    session_id: str | None = None,
    source: Source = "mcp",
) -> list[EntryPoint]:
    """Semantic entry: map a natural-language query to graph nodes to walk from.

    Backboard ranks the hits; we preserve that order and join each hit back to
    its `memoryIndex` node for the deterministic id. Hits with no index doc are
    dropped — an entry point must be a node you can actually walk. Backboard
    failures propagate (unlike `find_related_context`, there is no structural
    source to degrade to here — the caller asked to enter *semantically*).

    Passing `session_id` emits one `entry` traversal event per landed node so a
    graph view can highlight where the agent entered; omit it and emission is a
    no-op (`source` is then irrelevant). `source` tags where the call originated.
    """
    result = await bb.search_memories(assistant_id, query, limit=limit)
    hits = result.get("memories", []) if isinstance(result, dict) else []

    ordered_ids: list[str] = []  # Backboard rank order, deduped
    scores: dict[str, float | None] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        hit_id = _hit_id(hit)
        if hit_id is None or hit_id in scores:
            continue
        ordered_ids.append(hit_id)
        scores[hit_id] = _hit_score(hit)

    if not ordered_ids:
        return []

    docs = await _entry_lookup(org_id, repo_id, ordered_ids)
    by_bb = {d.bbMemoryId: d for d in docs}

    entries: list[EntryPoint] = []
    for hit_id in ordered_ids:  # rank order, not the join's arbitrary order
        doc = by_bb.get(hit_id)
        if doc is None:
            continue  # unindexed hit — nothing to walk from
        entries.append(
            EntryPoint(
                nodeId=decision_id(doc.id),
                type="decision",
                label=short_label(doc.contentSnapshot),
                score=scores[hit_id],
                # isCommunityReport stays False until community reports land (T4b).
            )
        )
    # One entry event per landed node, so a graph view can highlight where the
    # agent entered. A no-op without a session (tag is None).
    tag = TraversalTag(session_id, source) if session_id else None
    if tag is not None:
        for entry in entries:
            traversal_channel.publish(
                tag,
                kind="entry",
                node_id=entry.nodeId,
                edge_kind=None,
                from_node_id=None,
            )
    return entries


# --- walk_graph --------------------------------------------------------------


def _object_id(value: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except Exception as exc:  # invalid hex embedded in a node id
        raise ValueError(f"invalid object id: {value!r}") from exc


def _decision_neighbor(
    doc: MemoryIndex, *, symbols: list[str] | None = None
) -> WalkNeighbor:
    """A decision node as a neighbor, carrying the fields agents reason over."""
    return WalkNeighbor(
        nodeId=decision_id(doc.id),
        type="decision",
        label=short_label(doc.contentSnapshot),
        contentSnapshot=doc.contentSnapshot,
        stalenessStatus=doc.stalenessStatus,
        confidence=doc.confidence,
        prNumber=doc.prNumber,
        feature=doc.feature,
        symbols=symbols,
        # hasCommunityReport stays False until community reports land (T4b): a
        # report covering this node would let the agent zoom out instead of only
        # sideways to peers.
    )


def _finalize_groups(
    groups: dict[WalkEdgeKind, list[WalkNeighbor]], exclude: str | None
) -> tuple[dict[WalkEdgeKind, list[WalkNeighbor]], bool]:
    """Drop the excluded node, empty groups, and cap each group's size."""
    truncated = False
    out: dict[WalkEdgeKind, list[WalkNeighbor]] = {}
    for kind, items in groups.items():
        if exclude is not None:
            items = [n for n in items if n.nodeId != exclude]
        if not items:
            continue
        if len(items) > WALK_GROUP_LIMIT:
            items = items[:WALK_GROUP_LIMIT]
            truncated = True
        out[kind] = items
    return out, truncated


async def _load_one_decision(
    org_id: PydanticObjectId, memory_id: str
) -> MemoryIndex | None:
    docs = await _find_decisions(
        {"_id": _object_id(memory_id), "orgId": org_id, "deletedAt": None}
    )
    return docs[0] if docs else None


async def _neighbors_of(
    node_id: str,
    org_id: PydanticObjectId,
    edge_kinds: frozenset[WalkEdgeKind] | None,
    *,
    doc: MemoryIndex | None,
    exclude: str | None,
    tag: TraversalTag | None = None,
) -> tuple[dict[WalkEdgeKind, list[WalkNeighbor]], bool]:
    """One node's neighbors grouped by edge kind. `edge_kinds` filters *before*
    querying (directed traversal — an unwanted kind costs no query at all).

    `doc` is the pre-loaded MemoryIndex for a decision origin (its structural
    edges come straight off the fields, no extra query); virtual origins
    (file/pr/engineer/feature) exist only as a projection, so they query for the
    decisions that would emit the edge.
    """
    parsed = parse_node_id(node_id)

    def wanted(kind: WalkEdgeKind) -> bool:
        return edge_kinds is None or kind in edge_kinds

    groups: dict[WalkEdgeKind, list[WalkNeighbor]] = {}

    if parsed.type == "decision":
        assert doc is not None
        repo = doc.anchors.repo
        edge_symbols = doc.anchors.symbols or None

        if wanted("governs") and doc.anchors.files:
            groups["governs"] = [
                WalkNeighbor(
                    nodeId=file_id(repo, path),
                    type="file",
                    label=posixpath.basename(path) or path,
                    symbols=edge_symbols,
                )
                for path in doc.anchors.files
            ]
        if wanted("introduced") and doc.prNumber is not None:
            groups["introduced"] = [
                WalkNeighbor(
                    nodeId=pr_id(repo, doc.prNumber),
                    type="pr",
                    label=f"PR #{doc.prNumber}",
                    prNumber=doc.prNumber,
                )
            ]
        if wanted("made") and doc.authorUserId:
            names = await _names_by_user_id({doc.authorUserId})
            groups["made"] = [
                WalkNeighbor(
                    nodeId=engineer_id(doc.authorUserId),
                    type="engineer",
                    label=names.get(doc.authorUserId, "unknown"),
                )
            ]
        if wanted("belongs_to") and doc.feature:
            groups["belongs_to"] = [
                WalkNeighbor(
                    nodeId=feature_id(org_id, doc.feature),
                    type="feature",
                    label=doc.feature,
                    feature=doc.feature,
                )
            ]
        if wanted("superseded_by") and doc.supersededBy:
            target = await _load_one_decision(org_id, str(doc.supersededBy))
            if target is not None:  # dangling target dropped, build_graph parity
                groups["superseded_by"] = [_decision_neighbor(target)]
        if wanted("supersedes"):
            incoming = await _find_decisions(
                {"supersededBy": doc.id, "orgId": org_id, "deletedAt": None}
            )
            if incoming:
                groups["supersedes"] = [_decision_neighbor(d) for d in incoming]

    elif parsed.type == "file" and wanted("governs"):
        docs = await _find_decisions(
            {
                "orgId": org_id,
                "deletedAt": None,
                "anchors.repo": parsed.repo,
                "anchors.files": parsed.rest,
            }
        )
        if docs:
            groups["governs"] = [
                _decision_neighbor(d, symbols=d.anchors.symbols or None) for d in docs
            ]

    elif parsed.type == "pr" and wanted("introduced"):
        docs = await _find_decisions(
            {
                "orgId": org_id,
                "deletedAt": None,
                "anchors.repo": parsed.repo,
                "prNumber": int(parsed.rest),
            }
        )
        if docs:
            groups["introduced"] = [_decision_neighbor(d) for d in docs]

    elif parsed.type == "engineer" and wanted("made"):
        docs = await _find_decisions(
            {
                "orgId": org_id,
                "deletedAt": None,
                "authorUserId": _object_id(parsed.rest),
            }
        )
        if docs:
            groups["made"] = [_decision_neighbor(d) for d in docs]

    elif parsed.type == "feature" and wanted("belongs_to"):
        docs = await _find_decisions(
            {"orgId": org_id, "deletedAt": None, "feature": parsed.rest}
        )
        if docs:
            groups["belongs_to"] = [_decision_neighbor(d) for d in docs]

    out, truncated = _finalize_groups(groups, exclude)
    # One hop event per neighbor actually returned (origin excluded, per-kind
    # caps applied) so a graph view animates in lockstep with the walk — fired
    # here, per directed hop, so depth-2 sub-walks animate too. `node_id` is the
    # origin of these edges (the hop's `fromNodeId`). A no-op without a session.
    if tag is not None:
        for kind, neighbors in out.items():
            for neighbor in neighbors:
                traversal_channel.publish(
                    tag,
                    kind="hop",
                    node_id=neighbor.nodeId,
                    edge_kind=kind,
                    from_node_id=node_id,
                )
    return out, truncated


async def _neighbors_for_node_id(
    node_id: str,
    org_id: PydanticObjectId,
    edge_kinds: frozenset[WalkEdgeKind] | None,
    *,
    exclude: str | None,
    tag: TraversalTag | None = None,
) -> tuple[dict[WalkEdgeKind, list[WalkNeighbor]], bool]:
    """Load whatever a node id needs, then delegate to `_neighbors_of` — used
    for the second hop, where we hold ids but not the pre-loaded docs."""
    parsed = parse_node_id(node_id)
    doc = None
    if parsed.type == "decision":
        doc = await _load_one_decision(org_id, parsed.rest)
        if doc is None:  # neighbor decision vanished mid-walk; skip it
            return {}, False
    return await _neighbors_of(
        node_id, org_id, edge_kinds, doc=doc, exclude=exclude, tag=tag
    )


async def walk_graph(
    node_id: str,
    *,
    org_id: PydanticObjectId,
    edge_kinds: frozenset[WalkEdgeKind] | None = None,
    depth: int = 1,
    session_id: str | None = None,
    source: Source = "mcp",
) -> GraphWalk:
    """Directed local walk from `node_id`, neighbors grouped by edge kind.

    `edge_kinds=None` returns every kind; pass a subset to follow specific
    relationships (e.g. `{"superseded_by"}` to trace an evolution chain).
    `depth=2` expands each neighbor one further hop (the walk origin is excluded
    from those sub-groups); depth is hard-capped at 2 to bound context.

    Passing `session_id` emits one `hop` traversal event per neighbor returned
    (including the depth-2 sub-hops) so a graph view can animate the walk; omit
    it and emission is a no-op. `source` tags where the call originated.
    """
    if depth < 1 or depth > WALK_MAX_DEPTH:
        raise ValueError(f"depth must be 1..{WALK_MAX_DEPTH}, got {depth}")

    parsed = parse_node_id(node_id)  # raises ValueError on a malformed id
    if parsed.type == "feature" and parsed.repo != str(org_id):
        raise ValueError(f"feature node {node_id!r} is not in org {org_id}")

    doc: MemoryIndex | None = None
    if parsed.type == "decision":
        doc = await _load_one_decision(org_id, parsed.rest)
        if doc is None:
            raise ValueError(f"unknown decision node: {node_id!r}")
        origin_label = short_label(doc.contentSnapshot)
        origin_snapshot: str | None = doc.contentSnapshot
    else:
        origin_label = _virtual_label(parsed.type, parsed.rest)
        origin_snapshot = None

    tag = TraversalTag(session_id, source) if session_id else None
    groups, truncated = await _neighbors_of(
        node_id, org_id, edge_kinds, doc=doc, exclude=None, tag=tag
    )

    if depth == WALK_MAX_DEPTH:
        second_hop_truncated = await _expand_second_hop(
            groups, org_id, edge_kinds, origin=node_id, tag=tag
        )
        truncated = truncated or second_hop_truncated

    return GraphWalk(
        nodeId=node_id,
        type=parsed.type,
        label=origin_label,
        contentSnapshot=origin_snapshot,
        neighbors=groups,
        truncated=truncated,
        # hasCommunityReport stays False until community reports land (T4b).
    )


def _virtual_label(node_type: str, rest: str) -> str:
    """Label for a projected node the walk started on (matches build_graph)."""
    if node_type == "file":
        return posixpath.basename(rest) or rest
    if node_type == "pr":
        return f"PR #{rest}"
    return rest  # engineer id / feature name — best available without a query


async def _expand_second_hop(
    groups: dict[WalkEdgeKind, list[WalkNeighbor]],
    org_id: PydanticObjectId,
    edge_kinds: frozenset[WalkEdgeKind] | None,
    *,
    origin: str,
    tag: TraversalTag | None = None,
) -> bool:
    """Attach each neighbor's own neighbors (origin excluded); return whether
    any sub-group was capped."""
    all_neighbors = [n for items in groups.values() for n in items]
    if not all_neighbors:
        return False
    sub_results = await asyncio.gather(
        *(
            _neighbors_for_node_id(
                n.nodeId, org_id, edge_kinds, exclude=origin, tag=tag
            )
            for n in all_neighbors
        )
    )
    truncated = False
    for neighbor, (sub_groups, sub_truncated) in zip(all_neighbors, sub_results):
        if sub_groups:
            neighbor.neighbors = sub_groups
        truncated = truncated or sub_truncated
    return truncated
