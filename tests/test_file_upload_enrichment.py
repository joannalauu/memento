import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.file_upload import enrichment
from app.file_upload.enrichment import (
    _parse_claims,
    _skeleton_files,
    enrich_document,
    extract_decision_claims,
)

TREE = (
    "app/main.py  (blob, 123 B)\n"
    "app/context_engine  (tree)\n"
    "app/context_engine/anchors.py  (blob, 4592 B)\n"
    "WARNING: tree truncated by GitHub (>100k entries); listing is incomplete."
)


def _bb_returning(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(content=text))
    )


# ─── pure helpers ─────────────────────────────────────────────────────────────


def test_skeleton_files_keeps_blobs_drops_trees_and_warnings():
    assert _skeleton_files(TREE) == {
        "app/main.py",
        "app/context_engine/anchors.py",
    }


def test_parse_claims_skips_malformed_and_blank():
    raw = json.dumps(
        [
            {"claim": "use asyncpg", "files": ["app/main.py"], "symbols": ["get_db"]},
            {"not": "a claim"},
            {"claim": "   "},
        ]
    )
    claims = _parse_claims(raw)
    assert [c.claim for c in claims] == ["use asyncpg"]


@pytest.mark.parametrize("bad", [None, "", "not json", "{}", '"a string"'])
def test_parse_claims_non_array_is_empty(bad):
    assert _parse_claims(bad) == []


def test_parse_claims_strips_code_fence():
    assert _parse_claims('```json\n[{"claim": "x"}]\n```')[0].claim == "x"


# ─── extraction (grounding) ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_extract_filters_hallucinated_paths():
    bb = _bb_returning(
        json.dumps(
            [
                {
                    "claim": "auth in middleware",
                    "files": ["app/main.py", "does/not/exist.py"],
                    "symbols": ["get_current_user"],
                }
            ]
        )
    )
    claims = await extract_decision_claims(
        "doc text",
        TREE,
        bb=bb,
        assistant_id="a",
        valid_files=_skeleton_files(TREE),
    )
    assert len(claims) == 1
    # the invented path is dropped; the real one and symbols survive
    assert claims[0].files == ["app/main.py"]
    assert claims[0].symbols == ["get_current_user"]
    # closed-world call
    _, kwargs = bb.send_message.call_args
    assert kwargs["memory"] == "off" and kwargs["json_output"] is True


@pytest.mark.anyio
async def test_extract_swallows_backboard_error():
    bb = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    claims = await extract_decision_claims(
        "doc", TREE, bb=bb, assistant_id="a", valid_files=set()
    )
    assert claims == []


# ─── orchestration (writes) ───────────────────────────────────────────────────


def _stub_toolset(monkeypatch, tree=TREE):
    async def _list_tree(_args):
        return tree

    monkeypatch.setattr(
        enrichment,
        "build_github_toolset",
        lambda org, repo, gh: ([], {"list_tree": _list_tree}),
    )


@pytest.mark.anyio
async def test_enrich_writes_tagged_memory_per_claim(monkeypatch):
    _stub_toolset(monkeypatch)
    bb = _bb_returning(
        json.dumps([{"claim": "use asyncpg", "files": ["app/main.py"], "symbols": []}])
    )
    bb.add_memory = AsyncMock(return_value=SimpleNamespace())
    org = SimpleNamespace(id="org1", bbAssistantId="assist")
    repo = SimpleNamespace(id="repo1", owner="acme", name="api")
    entry = SimpleNamespace(filename="adr-001.md", id="doc1")

    written = await enrich_document(
        entry, doc_text="some decisions", org=org, repo=repo, bb=bb, github=object()
    )

    assert len(written) == 1
    _, kwargs = bb.add_memory.call_args
    assert kwargs["source"] == "legacy_doc"
    assert kwargs["confidence"] == "unverified"
    assert kwargs["files"] == ["app/main.py"]
    assert kwargs["repo"] == "acme/api"
    assert kwargs["metadata"]["doc"] == "adr-001.md"
    assert kwargs["content"] == "use asyncpg"


@pytest.mark.anyio
async def test_enrich_noops_on_empty_text(monkeypatch):
    _stub_toolset(monkeypatch)
    bb = SimpleNamespace(send_message=AsyncMock(), add_memory=AsyncMock())
    written = await enrich_document(
        entry=SimpleNamespace(filename="x", id="1"),
        doc_text="   ",
        org=SimpleNamespace(id="o", bbAssistantId="a"),
        repo=SimpleNamespace(id="r", owner="acme", name="api"),
        bb=bb,
        github=object(),
    )
    assert written == []
    bb.send_message.assert_not_awaited()
    bb.add_memory.assert_not_awaited()


@pytest.mark.anyio
async def test_enrich_survives_single_write_failure(monkeypatch):
    _stub_toolset(monkeypatch)
    bb = _bb_returning(
        json.dumps(
            [
                {"claim": "a", "files": ["app/main.py"], "symbols": []},
                {"claim": "b", "files": [], "symbols": []},
            ]
        )
    )
    bb.add_memory = AsyncMock(side_effect=[RuntimeError("db down"), SimpleNamespace()])
    written = await enrich_document(
        entry=SimpleNamespace(filename="x", id="1"),
        doc_text="decisions",
        org=SimpleNamespace(id="o", bbAssistantId="a"),
        repo=SimpleNamespace(id="r", owner="acme", name="api"),
        bb=bb,
        github=object(),
    )
    # first write raised, second still landed
    assert len(written) == 1
    assert bb.add_memory.await_count == 2
