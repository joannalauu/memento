import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.context_engine.consistency import check_consistency
from app.context_engine.schemas import RelatedMemory


def related_memory(bb_memory_id, pr_number=None):
    return RelatedMemory(
        bbMemoryId=bb_memory_id,
        content=f"content of {bb_memory_id}",
        score=3.0,
        prNumber=pr_number,
    )


def verdict_json(**overrides):
    verdict = {
        "verdict": "consistent",
        "confidence": "high",
        "conflicts": [],
        "reasoning": "extends the prior approach",
        "supersedes": [],
    }
    verdict.update(overrides)
    return json.dumps(verdict)


def conflict(bb_memory_id):
    return {
        "bbMemoryId": bb_memory_id,
        "priorDecision": "rate limiting lives in the gateway",
        "priorPr": 142,
        "nature": "moves rate limiting back into the service",
        "severity": "direct",
    }


@pytest.fixture
def bb():
    mock = AsyncMock()
    mock.send_message.return_value = SimpleNamespace(content=verdict_json())
    return mock


@pytest.mark.parametrize(
    ("mode", "fragment"), [("audit", "already merged"), ("preflight", "NOT been made")]
)
async def test_prompt_call_shape_and_mode_line(bb, mode, fragment):
    result = await check_consistency(
        "move rate limiting into the service",
        [related_memory("mem-1")],
        mode=mode,
        bb=bb,
        assistant_id="assistant-1",
    )

    kwargs = bb.send_message.await_args.kwargs
    assert kwargs["memory"] == "off"
    assert kwargs["json_output"] is True
    prompt = bb.send_message.await_args.args[0]
    assert fragment in prompt
    assert "mem-1" in prompt  # memories serialized into the prompt
    assert result.verdict == "consistent"
    assert result.confidence == "high"


async def test_conflict_id_whitelist(bb):
    bb.send_message.return_value = SimpleNamespace(
        content=verdict_json(
            verdict="conflict",
            conflicts=[conflict("mem-1"), conflict("mem-hallucinated")],
            supersedes=[142, 999],
        )
    )

    result = await check_consistency(
        "change",
        [related_memory("mem-1", pr_number=142)],
        mode="audit",
        bb=bb,
        assistant_id="assistant-1",
    )

    assert result.verdict == "conflict"
    assert [c.bbMemoryId for c in result.conflicts] == ["mem-1"]
    # supersedes filtered to PR numbers present in the input set
    assert result.supersedes == [142]


async def test_all_conflicts_hallucinated_downgrades(bb):
    bb.send_message.return_value = SimpleNamespace(
        content=verdict_json(verdict="conflict", conflicts=[conflict("mem-invented")])
    )

    result = await check_consistency(
        "change", [related_memory("mem-1")], mode="audit", bb=bb, assistant_id="a-1"
    )

    assert result.verdict == "consistent"
    assert result.confidence == "low"
    assert result.conflicts == []


@pytest.mark.parametrize(
    "content",
    [
        "I think this change looks fine overall.",  # prose
        '```json\n{"verdict": "consistent",\n```',  # fenced but broken
        None,
    ],
)
async def test_malformed_response_falls_back(bb, content):
    bb.send_message.return_value = SimpleNamespace(content=content)

    result = await check_consistency(
        "change", [related_memory("mem-1")], mode="preflight", bb=bb, assistant_id="a-1"
    )

    assert result.verdict == "no_prior_context"
    assert result.confidence == "low"


async def test_fenced_valid_json_still_parses(bb):
    bb.send_message.return_value = SimpleNamespace(
        content=f"```json\n{verdict_json()}\n```"
    )

    result = await check_consistency(
        "change", [related_memory("mem-1")], mode="audit", bb=bb, assistant_id="a-1"
    )

    assert result.verdict == "consistent"
    assert result.confidence == "high"


async def test_empty_related_short_circuits(bb):
    result = await check_consistency(
        "change", [], mode="preflight", bb=bb, assistant_id="a-1"
    )

    assert result.verdict == "no_prior_context"
    assert result.confidence == "high"
    assert bb.send_message.await_count == 0
