"""Context engine: the library that links code changes to memories.

A pipeline of three composable stages — callers take the prefix they need:

    extract_anchors(diff)        pure: diff -> Anchors (files + symbols)
    find_related_context(...)    async: anchors -> ranked list[RelatedMemory]
    check_consistency(...)       async: change + related -> ConsistencyVerdict

Plus a standalone freshness probe and its cached background sweep:

    staleness_check(memory, ...)  async: memory -> StalenessVerdict (fresh/stale/gap)
    sweep_repo_staleness(...)     async: precompute + cache verdicts onto memoryIndex

The pipeline stages and `staleness_check` are read-only. The sweep
(`refresh_staleness` / `sweep_repo_staleness`) is the one write path — it stamps
cached staleness onto memoryIndex so the graph renders without calling GitHub.
"""

from app.context_engine.anchors import extract_anchors
from app.context_engine.consistency import check_consistency
from app.context_engine.retrieval import find_related_context
from app.context_engine.schemas import (
    ConsistencyConflict,
    ConsistencyMode,
    ConsistencyVerdict,
    RelatedMemory,
    StalenessVerdict,
)
from app.context_engine.staleness import staleness_check
from app.context_engine.staleness_sweep import refresh_staleness, sweep_repo_staleness

__all__ = [
    "ConsistencyConflict",
    "ConsistencyMode",
    "ConsistencyVerdict",
    "RelatedMemory",
    "StalenessVerdict",
    "check_consistency",
    "extract_anchors",
    "find_related_context",
    "refresh_staleness",
    "staleness_check",
    "sweep_repo_staleness",
]
