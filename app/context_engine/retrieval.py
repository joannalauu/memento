"""Related-context retrieval: given anchors, find what we already know.

Unions two retrieval sources with complementary strengths:

- structural (precise): memories whose `memoryIndex.anchors` contain one of
  the diff's files/symbols exactly — a deliberate "governs" edge, answered by
  the existing `anchors.files`/`anchors.symbols` Mongo indexes;
- semantic (fuzzy): one `Backboard.search_memories` query per anchor, so each
  file and symbol gets focused retrieval instead of one blended-mush query.

Results are deduped by bbMemoryId with evidence unioned — how many different
anchors a memory matches is itself the strongest relevance signal. Semantic
hits are joined back to `memoryIndex` for structure and repo scoping;
`search_memories` has no repo filter, so unindexed hits are kept only when
their metadata carries the right repo tag. Individual semantic failures
degrade to structural-only results, never raise. Read-only: writes nothing.
"""

import asyncio
import logging
from typing import Any

from beanie import PydanticObjectId

from app.backboard.client import Backboard
from app.backboard.models import Anchors, MemoryIndex
from app.context_engine.schemas import RelatedMemory

logger = logging.getLogger(__name__)

# Untuned priors — the structure (accumulation + exact-beats-semantic
# tie-break) is load-bearing, the numbers are adjustable.
FILE_MATCH_WEIGHT = 3.0  # exact file anchor hit: deliberate "governs" edge
SYMBOL_MATCH_WEIGHT = 2.0  # exact symbol hit: strong, but names collide
SEMANTIC_MATCH_WEIGHT = 1.0  # embedding similarity: useful recall, noisiest
SEMANTIC_LIMIT_PER_ANCHOR = 5
MAX_RESULTS = 20


async def _structural_query(
    repo_id: PydanticObjectId, files: list[str], symbols: list[str]
) -> list[MemoryIndex]:
    """Active memories whose anchors exactly contain any of the given
    files/symbols. One round trip; per-anchor attribution happens in Python."""
    return await MemoryIndex.find(
        {
            "repoId": repo_id,
            "deletedAt": None,
            "$or": [
                {"anchors.files": {"$in": files}},
                {"anchors.symbols": {"$in": symbols}},
            ],
        }
    ).to_list()


async def _index_lookup(
    repo_id: PydanticObjectId, bb_memory_ids: list[str]
) -> list[MemoryIndex]:
    """Join semantic hits back to their index docs (repo-scoped, active only)."""
    return await MemoryIndex.find(
        {
            "bbMemoryId": {"$in": bb_memory_ids},
            "repoId": repo_id,
            "deletedAt": None,
        }
    ).to_list()


def _entry_from_index(doc: MemoryIndex, anchors: Anchors) -> RelatedMemory:
    matched_files = [f for f in anchors.files if f in doc.anchors.files]
    matched_symbols = [s for s in anchors.symbols if s in doc.anchors.symbols]
    return RelatedMemory(
        bbMemoryId=doc.bbMemoryId,
        content=doc.contentSnapshot,
        matchedFiles=matched_files,
        matchedSymbols=matched_symbols,
        score=0.0,  # scored after all evidence is unioned
        prNumber=doc.prNumber,
        feature=doc.feature,
        authorUserId=doc.authorUserId,
        source=doc.source,
        confidence=doc.confidence,
    )


def _hit_id(hit: dict[str, Any]) -> str | None:
    raw = hit.get("id") or hit.get("memory_id")
    return str(raw) if raw else None


async def find_related_context(
    anchors: Anchors,
    *,
    bb: Backboard,
    assistant_id: str,
    repo_id: PydanticObjectId,
    semantic_limit: int = SEMANTIC_LIMIT_PER_ANCHOR,
    max_results: int = MAX_RESULTS,
) -> list[RelatedMemory]:
    """Rank the memories most relevant to the given anchors.

    score = 3·|matchedFiles| + 2·|matchedSymbols| + 1·|semanticAnchors|:
    weights encode match quality, summation encodes breadth (a memory touching
    two anchors outranks a stronger match on one). Ties break on exact-match
    count, then bbMemoryId for determinism. Truncated to `max_results` so the
    consistency prompt stays bounded — only the weakest evidence is dropped.
    """
    merged: dict[str, RelatedMemory] = {}

    # structural: exact anchor matches from the local index
    if anchors.files or anchors.symbols:
        for doc in await _structural_query(repo_id, anchors.files, anchors.symbols):
            merged[doc.bbMemoryId] = _entry_from_index(doc, anchors)

    # semantic: one focused search per anchor, failures skipped
    all_anchors = [*anchors.files, *anchors.symbols]
    results = await asyncio.gather(
        *(
            bb.search_memories(
                assistant_id, f"{anchors.repo} {a}", limit=semantic_limit
            )
            for a in all_anchors
        ),
        return_exceptions=True,
    )
    semantic_hits: dict[str, dict[str, Any]] = {}  # id -> raw hit (first seen)
    semantic_anchors: dict[str, list[str]] = {}  # id -> anchors that surfaced it
    for anchor, result in zip(all_anchors, results):
        if isinstance(result, BaseException):
            logger.warning("semantic search for %r failed: %s", anchor, result)
            continue
        for hit in result.get("memories", []):
            if not isinstance(hit, dict):
                continue
            hit_id = _hit_id(hit)
            if hit_id is None:
                continue
            semantic_hits.setdefault(hit_id, hit)
            found = semantic_anchors.setdefault(hit_id, [])
            if anchor not in found:
                found.append(anchor)

    # join semantic hits to index docs for structure + repo scoping
    unseen = [hit_id for hit_id in semantic_hits if hit_id not in merged]
    if unseen:
        for doc in await _index_lookup(repo_id, unseen):
            merged[doc.bbMemoryId] = _entry_from_index(doc, anchors)
    for hit_id, hit in semantic_hits.items():
        if hit_id not in merged:
            # unindexed hit: metadata repo tag is the only scoping we have
            metadata = hit.get("metadata") or {}
            if metadata.get("repo") != anchors.repo:
                continue
            merged[hit_id] = RelatedMemory(
                bbMemoryId=hit_id, content=str(hit.get("content") or ""), score=0.0
            )
        merged[hit_id].semanticAnchors = semantic_anchors[hit_id]

    for entry in merged.values():
        entry.score = (
            FILE_MATCH_WEIGHT * len(entry.matchedFiles)
            + SYMBOL_MATCH_WEIGHT * len(entry.matchedSymbols)
            + SEMANTIC_MATCH_WEIGHT * len(entry.semanticAnchors)
        )
    ranked = sorted(
        merged.values(),
        key=lambda e: (
            -e.score,
            -(len(e.matchedFiles) + len(e.matchedSymbols)),
            e.bbMemoryId,
        ),
    )
    return ranked[:max_results]
