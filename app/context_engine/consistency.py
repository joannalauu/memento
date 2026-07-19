"""Consistency judgment: one closed-world LLM call over retrieved memories.

Takes a change plus the memories `find_related_context` chose and asks the
assistant for a single verdict: consistent, in conflict with cited prior
decisions, or no prior context. The call runs with ``memory="off"`` — the
model's world is precisely the memories we pass, so the verdict is
deterministic, auditable ("these N memories produced it"), and can never cite
an id we can't map back. For the same reason it deliberately does NOT use the
tool-executor loop (app/backboard/executor.py): tools would reopen the world.

The prompt separates supersession (a change that intentionally replaces an
old decision — healthy) from conflict (unknowingly breaking one — the thing
worth warning about). Prompt rules are not trusted: cited memory ids and
superseded PR numbers are whitelisted against the input set in code, and a
malformed model response degrades to a low-confidence "no_prior_context"
instead of raising — a bad verdict must never block a merge from being
recorded or an engineer from editing.
"""

import json
import re

from pydantic import ValidationError

from app.backboard.client import (
    CLOSED_WORLD_LLM_PROVIDER,
    CLOSED_WORLD_MODEL_NAME,
    Backboard,
)
from app.backboard.executor import final_text
from app.backboard.models import Anchors
from app.context_engine.schemas import (
    ConsistencyMode,
    ConsistencyVerdict,
    RelatedMemory,
)

_MODE_LINES: dict[ConsistencyMode, str] = {
    "audit": (
        "MODE: audit — this change is already merged. Your verdict annotates "
        "its permanent decision record; focus on accurately identifying "
        "supersession and conflicts for the historical record."
    ),
    "preflight": (
        "MODE: preflight — this change has NOT been made yet. Your verdict "
        "will be shown to the engineer before they proceed; write `reasoning` "
        "as a direct heads-up they can act on."
    ),
}

CONSISTENCY_PROMPT = """\
You are a codebase consistency reviewer. You are given a code change and a set
of prior decision records retrieved because they concern the same files or
symbols. Judge ONLY whether the change is consistent with those prior decisions.

## Inputs

{mode_line}

CHANGE:
{change}

ANCHORS:
{anchors}

PRIOR DECISIONS (retrieved by shared anchors — reason ONLY over these):
{memories}

## How to judge

1. For each prior decision, ask: does this change contradict what it established?
   - It CONTRADICTS if the change reverses, removes, or diverges from the approach,
     constraint, or rationale the decision recorded.
   - It does NOT contradict if the change extends, uses, or is unrelated to it,
     or merely touches the same file without affecting the decision's substance.

2. Separate two cases that look similar but are opposite in meaning:
   - SUPERSESSION (intentional): the change clearly, deliberately replaces an
     older decision — e.g. the description says so, or the change wholesale
     rebuilds the area the decision governed. Record the old PR number in
     `supersedes`. Do NOT also list it as a conflict.
   - CONFLICT (unintentional): the change breaks or erodes a prior decision with
     no sign the author knew it existed. This is what matters most — list it in
     `conflicts`.
   When unsure which, treat it as a conflict with severity "partial" and lower
   your confidence — flagging for a human is safer than silently allowing drift.

3. If no prior decision bears on the change, return "no_prior_context". Do not
   manufacture a conflict to seem useful. Absence of related context is a valid,
   common, honest answer.

## Rules

- Reason ONLY over the prior decisions provided. Do not use outside knowledge of
  the codebase.
- Every `bbMemoryId` you output MUST be copied exactly from an input decision's
  `bbMemoryId`. Never invent an ID. If you cannot cite a specific input decision,
  it is not a conflict.
- `reasoning` must be understandable to the change's author with no extra context.
- Output ONLY the JSON object, no preamble, no markdown fences.

## Output schema

{{"verdict": "consistent" | "conflict" | "no_prior_context",
  "confidence": "high" | "medium" | "low",
  "conflicts": [{{"bbMemoryId": str, "priorDecision": str, "priorPr": int | null,
                 "nature": str, "severity": "direct" | "partial"}}],
  "reasoning": str,
  "supersedes": [int]}}
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _build_prompt(
    change: str,
    related: list[RelatedMemory],
    mode: ConsistencyMode,
    anchors: Anchors | None,
) -> str:
    """Pure prompt assembly: one JSON object per memory so the model can only
    cite ids it was shown."""
    anchors_json = json.dumps(
        {"files": anchors.files, "symbols": anchors.symbols} if anchors else {}
    )
    memories = "\n".join(
        json.dumps(
            {
                "bbMemoryId": m.bbMemoryId,
                "content": m.content,
                "prNumber": m.prNumber,
                "feature": m.feature,
                "source": m.source,
                "confidence": m.confidence,
            }
        )
        for m in related
    )
    return CONSISTENCY_PROMPT.format(
        mode_line=_MODE_LINES[mode],
        change=change,
        anchors=anchors_json,
        memories=memories,
    )


def _parse_verdict(text: str | None) -> ConsistencyVerdict | None:
    if not text:
        return None
    try:
        return ConsistencyVerdict.model_validate(
            json.loads(_FENCE_RE.sub("", text.strip()))
        )
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


def _apply_guards(
    verdict: ConsistencyVerdict, related: list[RelatedMemory]
) -> ConsistencyVerdict:
    """Whitelist everything the model cited against the input set."""
    allowed_ids = {m.bbMemoryId for m in related}
    allowed_prs = {m.prNumber for m in related if m.prNumber is not None}
    verdict.conflicts = [c for c in verdict.conflicts if c.bbMemoryId in allowed_ids]
    verdict.supersedes = [p for p in verdict.supersedes if p in allowed_prs]
    if verdict.verdict == "conflict" and not verdict.conflicts:
        # a conflict claim with zero admissible evidence is noise
        verdict.verdict = "consistent"
        verdict.confidence = "low"
    return verdict


async def check_consistency(
    change: str,
    related: list[RelatedMemory],
    *,
    mode: ConsistencyMode,
    bb: Backboard,
    assistant_id: str,
    anchors: Anchors | None = None,
    model_name: str | None = None,
) -> ConsistencyVerdict:
    """Judge a change against its related memories with one memory="off" call.

    Never raises on model misbehavior (malformed JSON, hallucinated citations);
    Backboard transport errors propagate to the caller.
    """
    if not related:
        return ConsistencyVerdict(
            verdict="no_prior_context",
            confidence="high",
            reasoning="no related memories found",
        )
    response = await bb.send_message(
        _build_prompt(change, related, mode, anchors),
        assistant_id=assistant_id,
        memory="off",
        json_output=True,
        llm_provider=CLOSED_WORLD_LLM_PROVIDER,
        model_name=model_name or CLOSED_WORLD_MODEL_NAME,
    )
    verdict = _parse_verdict(final_text(response))
    if verdict is None:
        return ConsistencyVerdict(
            verdict="no_prior_context",
            confidence="low",
            reasoning="model response was not valid JSON",
        )
    return _apply_guards(verdict, related)
