import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.backboard.models import Anchors
from app.context_engine.schemas import RelatedMemory
from app.distillation.distill import (
    FALLBACK_FEATURE,
    _parse_output,
    build_prompt,
    distill,
)

ANCHORS = Anchors(
    repo="acme/api", files=["app/limits.py", "app/db.py"], symbols=["RateLimiter"]
)


def related(bb_memory_id="mem-1", **over):
    fields = dict(
        bbMemoryId=bb_memory_id,
        content=f"content of {bb_memory_id}",
        score=3.0,
        prNumber=42,
        feature="rate-limiting",
    )
    fields.update(over)
    return RelatedMemory(**fields)


def decision(**over):
    d = {
        "content": "Chose fixed-window over sliding-window for simplicity.",
        "anchors": {"files": ["app/limits.py"], "symbols": ["RateLimiter"]},
        "feature": "rate-limiting",
        "confidence": "high",
    }
    d.update(over)
    return d


def conflict(**over):
    c = {
        "bbMemoryId": "mem-1",
        "priorDecision": "Rate limits are enforced at the gateway.",
        "priorPr": 42,
        "nature": "This change enforces limits in-app instead.",
        "severity": "direct",
    }
    c.update(over)
    return c


def bb_returning(text):
    mock = AsyncMock()
    mock.send_message.return_value = SimpleNamespace(content=text)
    return mock


async def call_distill(bb, related_memories=()):
    return await distill(
        bb=bb,
        assistant_id="asst-1",
        pr_number=7,
        pr_title="Add rate limiting",
        branch="feat/x",
        pr_description="Adds a limiter.",
        anchors=ANCHORS,
        feature_names=["rate-limiting"],
        related=list(related_memories),
        transcript_block="[user] please add rate limiting",
    )


# --- parsing -----------------------------------------------------------------


def test_parse_happy_path():
    out = _parse_output(json.dumps({"decisions": [decision()], "conflicts": []}))
    assert out is not None
    assert len(out.decisions) == 1
    assert out.decisions[0].confidence == "high"


def test_parse_strips_markdown_fences():
    text = "```json\n" + json.dumps({"decisions": [], "conflicts": []}) + "\n```"
    assert _parse_output(text) is not None


@pytest.mark.parametrize("bad", [None, "", "not json", "[1, 2]", '"a string"'])
def test_parse_rejects_non_object(bad):
    assert _parse_output(bad) is None


def test_parse_skips_malformed_elements_keeps_rest():
    payload = {
        "decisions": [decision(), {"content": 5}, decision(confidence="bogus")],
        "conflicts": [conflict(), {"bbMemoryId": None}],
    }
    out = _parse_output(json.dumps(payload))
    assert out is not None
    assert len(out.decisions) == 1
    assert len(out.conflicts) == 1


def test_parse_tolerates_missing_keys():
    out = _parse_output("{}")
    assert out is not None
    assert out.decisions == [] and out.conflicts == []


# --- the call + guards ---------------------------------------------------------


async def test_distill_sends_closed_world_call():
    bb = bb_returning(json.dumps({"decisions": [], "conflicts": []}))

    out = await call_distill(bb)

    assert out is not None
    _, kwargs = bb.send_message.call_args
    assert kwargs["memory"] == "off"
    assert kwargs["json_output"] is True
    assert kwargs["assistant_id"] == "asst-1"


async def test_distill_returns_none_on_garbage():
    assert await call_distill(bb_returning("I could not comply.")) is None


async def test_guard_clamps_anchors_to_diff():
    payload = {
        "decisions": [
            decision(
                anchors={
                    "files": ["app/limits.py", "app/invented.py"],
                    "symbols": ["RateLimiter", "Ghost"],
                }
            )
        ],
        "conflicts": [],
    }
    out = await call_distill(bb_returning(json.dumps(payload)))

    assert out is not None
    assert out.decisions[0].anchors.files == ["app/limits.py"]
    assert out.decisions[0].anchors.symbols == ["RateLimiter"]


async def test_guard_whitelists_conflict_citations():
    payload = {
        "decisions": [],
        "conflicts": [conflict(), conflict(bbMemoryId="mem-invented")],
    }
    out = await call_distill(bb_returning(json.dumps(payload)), [related("mem-1")])

    assert out is not None
    assert [c.bbMemoryId for c in out.conflicts] == ["mem-1"]


async def test_guard_slugifies_features_and_drops_empty_content():
    payload = {
        "decisions": [
            decision(feature="Rate Limiting!"),
            decision(feature="???"),
            decision(content="   "),
        ],
        "conflicts": [],
    }
    out = await call_distill(bb_returning(json.dumps(payload)))

    assert out is not None
    assert [d.feature for d in out.decisions] == ["rate-limiting", FALLBACK_FEATURE]


# --- prompt ------------------------------------------------------------------


def test_prompt_contains_the_closed_world():
    prompt = build_prompt(
        pr_number=7,
        pr_title="Add rate limiting",
        branch="feat/x",
        pr_description="",
        anchors=ANCHORS,
        feature_names=["billing", "rate-limiting"],
        related=[related("mem-1"), related("mem-2")],
        transcript_block="[user] hello",
        dropped_sessions=2,
    )
    assert '"mem-1"' in prompt and '"mem-2"' in prompt
    assert '"rate-limiting"' in prompt and '"billing"' in prompt
    assert "app/limits.py" in prompt
    assert "2 older session(s) omitted" in prompt
    assert "(none)" in prompt  # empty description rendered explicitly
    assert prompt.index("[user] hello") > prompt.index('"mem-1"')
