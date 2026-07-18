from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine import retrieval
from app.context_engine.retrieval import find_related_context

REPO = "acme/api-server"
REPO_ID = PydanticObjectId()


def make_anchors(files=(), symbols=()):
    return Anchors(repo=REPO, files=list(files), symbols=list(symbols))


def make_index_doc(bb_memory_id, files=(), symbols=(), **fields):
    return MemoryIndex.model_construct(
        bbMemoryId=bb_memory_id,
        contentSnapshot=f"[repo: {REPO}] content of {bb_memory_id}",
        anchors=make_anchors(files, symbols),
        **fields,
    )


def semantic_result(*hits):
    return {"memories": list(hits), "total_count": len(hits)}


def hit(memory_id, repo=REPO):
    return {
        "id": memory_id,
        "content": f"content of {memory_id}",
        "metadata": {"repo": repo},
    }


@pytest.fixture
def bb():
    mock = AsyncMock()
    mock.search_memories.return_value = semantic_result()
    return mock


@pytest.fixture
def index_docs(monkeypatch):
    """Patch the module-level Mongo helpers; return the mutable doc lists."""
    structural: list[MemoryIndex] = []
    lookup: list[MemoryIndex] = []

    async def fake_structural(repo_id, files, symbols):
        return list(structural)

    async def fake_lookup(repo_id, ids):
        return [d for d in lookup if d.bbMemoryId in ids]

    monkeypatch.setattr(retrieval, "_structural_query", fake_structural)
    monkeypatch.setattr(retrieval, "_index_lookup", fake_lookup)
    return structural, lookup


async def test_ranking_file_beats_symbol_beats_semantic(bb, index_docs):
    structural, lookup = index_docs
    structural.append(make_index_doc("mem-file", files=["app/limits.py"]))
    structural.append(make_index_doc("mem-symbol", symbols=["RateLimiter"]))
    bb.search_memories.side_effect = [
        semantic_result(hit("mem-semantic")),  # app/limits.py query
        semantic_result(),  # RateLimiter query
    ]

    ranked = await find_related_context(
        make_anchors(files=["app/limits.py"], symbols=["RateLimiter"]),
        bb=bb,
        assistant_id="assistant-1",
        repo_id=REPO_ID,
    )

    assert [(m.bbMemoryId, m.score) for m in ranked] == [
        ("mem-file", 3.0),
        ("mem-symbol", 2.0),
        ("mem-semantic", 1.0),
    ]


async def test_multi_anchor_accumulation(bb, index_docs):
    structural, _ = index_docs
    structural.append(
        make_index_doc("mem-two-symbols", symbols=["RateLimiter", "acquire"])
    )
    structural.append(make_index_doc("mem-one-file", files=["app/limits.py"]))

    ranked = await find_related_context(
        make_anchors(files=["app/limits.py"], symbols=["RateLimiter", "acquire"]),
        bb=bb,
        assistant_id="assistant-1",
        repo_id=REPO_ID,
    )

    # 2 symbol matches (4.0) outrank 1 file match (3.0)
    assert [(m.bbMemoryId, m.score) for m in ranked] == [
        ("mem-two-symbols", 4.0),
        ("mem-one-file", 3.0),
    ]
    assert ranked[0].matchedSymbols == ["RateLimiter", "acquire"]


async def test_dedupe_union_across_sources(bb, index_docs):
    structural, _ = index_docs
    doc = make_index_doc(
        "mem-both", files=["app/limits.py"], prNumber=142, feature="rate-limiting"
    )
    structural.append(doc)
    bb.search_memories.side_effect = [
        semantic_result(hit("mem-both")),  # app/limits.py query
        semantic_result(hit("mem-both")),  # RateLimiter query
    ]

    ranked = await find_related_context(
        make_anchors(files=["app/limits.py"], symbols=["RateLimiter"]),
        bb=bb,
        assistant_id="assistant-1",
        repo_id=REPO_ID,
    )

    assert len(ranked) == 1
    entry = ranked[0]
    # one entry, evidence unioned, contributions summed: 3.0 + 1.0 + 1.0
    assert entry.matchedFiles == ["app/limits.py"]
    assert entry.semanticAnchors == ["app/limits.py", "RateLimiter"]
    assert entry.score == 5.0
    # structure joined from the index doc
    assert entry.prNumber == 142
    assert entry.feature == "rate-limiting"
    assert entry.content == doc.contentSnapshot


async def test_semantic_orphan_policy(bb, index_docs):
    bb.search_memories.return_value = semantic_result(
        hit("mem-same-repo"), hit("mem-other-repo", repo="acme/other")
    )

    ranked = await find_related_context(
        make_anchors(files=["app/limits.py"]),
        bb=bb,
        assistant_id="assistant-1",
        repo_id=REPO_ID,
    )

    # unindexed hit kept iff its metadata repo tag matches; structure stays None
    assert [m.bbMemoryId for m in ranked] == ["mem-same-repo"]
    assert ranked[0].prNumber is None
    assert ranked[0].source is None
    assert ranked[0].content == "content of mem-same-repo"


async def test_semantic_failure_degrades_to_structural(bb, index_docs):
    structural, _ = index_docs
    structural.append(make_index_doc("mem-file", files=["app/limits.py"]))
    bb.search_memories.side_effect = [
        RuntimeError("backboard blip"),
        semantic_result(hit("mem-semantic")),
    ]

    ranked = await find_related_context(
        make_anchors(files=["app/limits.py"], symbols=["RateLimiter"]),
        bb=bb,
        assistant_id="assistant-1",
        repo_id=REPO_ID,
    )

    # per-anchor fan-out: one search per file + per symbol
    assert bb.search_memories.await_count == 2
    assert [m.bbMemoryId for m in ranked] == ["mem-file", "mem-semantic"]
