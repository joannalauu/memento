"""Distillation: one closed-world LLM call turning transcripts into decisions.

Same discipline as app/context_engine/consistency.py — the call runs with
``memory="off"`` so the model's world is precisely what we hand it (the
transcripts, the PR, the anchors, the retrieved prior memories, the org's
feature list), Backboard's auto-retrieval can't contaminate the set, and no
tools reopen the world. Prompt rules are not trusted: decision anchors are
clamped to the diff's anchors, conflict citations are whitelisted against the
retrieved memories, and feature labels are slug-cased in code.

Parsing follows app/file_upload/enrichment.py's per-element tolerance: a
malformed decision or conflict is skipped, not fatal. Only an unparseable /
non-object response returns None — that's a distillation failure the job
layer retries (the call is expensive but idempotent). Transport errors
propagate; model misbehavior never raises.
"""

import json
import logging
import re

from pydantic import ValidationError

from app.backboard.client import (
    CLOSED_WORLD_LLM_PROVIDER,
    CLOSED_WORLD_MODEL_NAME,
    Backboard,
)
from app.backboard.executor import final_text
from app.backboard.models import Anchors
from app.context_engine.schemas import ConsistencyConflict, RelatedMemory
from app.distillation.schemas import DistillationOutput, DistilledDecision
from app.orgs.crud import slugify

logger = logging.getLogger(__name__)

# Feature for a decision whose label slugifies to nothing (e.g. "???").
FALLBACK_FEATURE = "general"

DISTILLATION_PROMPT = """\
You are distilling engineering decision records from a merged pull request and
the Claude Code session transcripts in which the work was done. The records you
produce become the team's permanent memory of WHY this change is the way it is.

## Inputs

PR: #{pr_number} "{pr_title}" (branch: {branch})

PR DESCRIPTION:
{pr_description}

ANCHORS (files and symbols this PR actually changed):
{anchors}

EXISTING FEATURES (this org's current feature labels):
{features}

PRIOR DECISIONS (existing memories about the same files/symbols — reason ONLY
over these when judging conflicts):
{memories}

TRANSCRIPTS ({transcript_note}):
{transcripts}

## What to extract

A decision is something a future engineer touching these files needs to know
and cannot recover from the code alone: a constraint honored, an alternative
rejected and why, a tradeoff accepted, a gotcha discovered, an invariant the
change relies on. Prefer the engineer's and assistant's own stated reasoning
from the transcripts over anything you infer.

- Do NOT record code narration ("added function X"), restatements of the diff,
  or generic best practices. If the transcripts contain no durable decisions,
  return an empty `decisions` list — that is a valid, honest answer.
- Each decision must stand alone: one or two sentences, understandable with no
  access to the transcript.
- `anchors`: the files/symbols the decision governs, copied EXACTLY from
  ANCHORS above. Never invent paths or symbols.
- `feature`: the product/system area the decision belongs to. Reuse an
  EXISTING FEATURES label when one fits; otherwise coin a new short
  kebab-case label (e.g. "rate-limiting").
- `confidence`: "high" when the transcript states the decision explicitly,
  "medium" when clearly implied, "low" when inferred.

## Conflicts

Separately, compare the merged change against PRIOR DECISIONS. Report a
conflict ONLY when the change contradicts a prior decision with no sign the
author knew it existed (an intentional, stated replacement is supersession —
not a conflict; leave it out). Every `bbMemoryId` you cite MUST be copied
exactly from a PRIOR DECISIONS entry. No admissible citation → no conflict.

## Output

Output ONLY the JSON object, no preamble, no markdown fences.

{{"decisions": [{{"content": str,
               "anchors": {{"files": [str], "symbols": [str]}},
               "feature": str,
               "confidence": "high" | "medium" | "low"}}],
  "conflicts": [{{"bbMemoryId": str, "priorDecision": str, "priorPr": int | null,
                "nature": str, "severity": "direct" | "partial"}}]}}
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def build_prompt(
    *,
    pr_number: int,
    pr_title: str,
    branch: str,
    pr_description: str,
    anchors: Anchors,
    feature_names: list[str],
    related: list[RelatedMemory],
    transcript_block: str,
    dropped_sessions: int,
) -> str:
    """Pure prompt assembly. Memories are one compact JSON object per line
    (like consistency.py) so the model can only cite ids it was shown."""
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
    transcript_note = "in capture order, oldest first"
    if dropped_sessions:
        transcript_note += f"; {dropped_sessions} older session(s) omitted for length"
    return DISTILLATION_PROMPT.format(
        pr_number=pr_number,
        pr_title=pr_title,
        branch=branch,
        pr_description=pr_description.strip() or "(none)",
        anchors=json.dumps({"files": anchors.files, "symbols": anchors.symbols}),
        features=json.dumps(feature_names),
        memories=memories or "(none)",
        transcripts=transcript_block,
        transcript_note=transcript_note,
    )


def _parse_output(text: str | None) -> DistillationOutput | None:
    """Tolerant parse: malformed elements are skipped; a response that is not
    a JSON object at all yields None (retryable failure)."""
    if not text:
        return None
    try:
        data = json.loads(_FENCE_RE.sub("", text.strip()))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    decisions: list[DistilledDecision] = []
    raw_decisions = data.get("decisions")
    for item in raw_decisions if isinstance(raw_decisions, list) else []:
        try:
            decisions.append(DistilledDecision.model_validate(item))
        except ValidationError:
            logger.warning("skipping malformed decision element: %.200r", item)

    conflicts: list[ConsistencyConflict] = []
    raw_conflicts = data.get("conflicts")
    for item in raw_conflicts if isinstance(raw_conflicts, list) else []:
        try:
            conflicts.append(ConsistencyConflict.model_validate(item))
        except ValidationError:
            logger.warning("skipping malformed conflict element: %.200r", item)

    return DistillationOutput(decisions=decisions, conflicts=conflicts)


def _apply_guards(
    output: DistillationOutput,
    anchors: Anchors,
    related: list[RelatedMemory],
) -> DistillationOutput:
    """Enforce in code what the prompt merely requests."""
    allowed_files = set(anchors.files)
    allowed_symbols = set(anchors.symbols)
    kept_decisions: list[DistilledDecision] = []
    for d in output.decisions:
        if not d.content.strip():
            continue
        d.anchors.files = [f for f in d.anchors.files if f in allowed_files]
        d.anchors.symbols = [s for s in d.anchors.symbols if s in allowed_symbols]
        d.feature = slugify(d.feature) or FALLBACK_FEATURE
        kept_decisions.append(d)
    output.decisions = kept_decisions

    allowed_ids = {m.bbMemoryId for m in related}
    output.conflicts = [c for c in output.conflicts if c.bbMemoryId in allowed_ids]
    return output


async def distill(
    *,
    bb: Backboard,
    assistant_id: str,
    pr_number: int,
    pr_title: str,
    branch: str,
    pr_description: str,
    anchors: Anchors,
    feature_names: list[str],
    related: list[RelatedMemory],
    transcript_block: str,
    dropped_sessions: int = 0,
    model_name: str | None = None,
) -> DistillationOutput | None:
    """One memory="off" call → guarded DistillationOutput, or None when the
    model's response wasn't a JSON object (caller retries the job)."""
    prompt = build_prompt(
        pr_number=pr_number,
        pr_title=pr_title,
        branch=branch,
        pr_description=pr_description,
        anchors=anchors,
        feature_names=feature_names,
        related=related,
        transcript_block=transcript_block,
        dropped_sessions=dropped_sessions,
    )
    response = await bb.send_message(
        prompt,
        assistant_id=assistant_id,
        memory="off",
        json_output=True,
        llm_provider=CLOSED_WORLD_LLM_PROVIDER,
        model_name=model_name or CLOSED_WORLD_MODEL_NAME,
    )
    output = _parse_output(final_text(response))
    if output is None:
        return None
    return _apply_guards(output, anchors, related)
