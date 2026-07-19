"""Doc-vs-code gap detection: open a gap chat where a legacy claim already
contradicts the current code.

Enrichment (see enrichment.py) turns an uploaded doc's decisions into
``legacy_doc`` memories, but a freshly-written memory has no ``commitSha``
baseline, so the git-diff `staleness_check` can't judge it — it reads every fresh
memory as ``fresh`` (app/context_engine/staleness.py). Whether a legacy claim
still matches the code is instead a *semantic* question: does the current code of
the files the claim governs contradict what the claim asserts?

This module answers that with one closed-world (``memory="off"``, ``json_output``)
call per memory — the same shape as the consistency judge
(app/context_engine/consistency.py) — reading the anchored files' current
contents via the GitHub toolset. A memory judged to conflict opens a gap chat
through the existing `open_gap_chat` (a synthetic ``gap`` verdict carries the
conflicting files and the current HEAD as the new staleness baseline), so the
engineer is asked to reconcile it exactly like the merge-driven path does.

Best-effort throughout, like enrichment: a judge miss, a bad file read, or a
Backboard/GitHub error degrades to "no gap opened", never an exception. A
per-document ceiling (``MAX_GAP_QUESTIONS_PER_DOC``) keeps one doc from flooding
the reviewer with questions; when it truncates, the drop is logged, not silent.
"""

import json
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError

from app.backboard.client import (
    CLOSED_WORLD_LLM_PROVIDER,
    CLOSED_WORLD_MODEL_NAME,
    Backboard,
)
from app.backboard.executor import final_text
from app.backboard.models import MemoryIndex
from app.context_engine.schemas import StalenessVerdict
from app.gap_chat.service import open_gap_chat
from app.github.client import GitHubApp
from app.github.history import build_repo_history
from app.github.tools import build_github_toolset
from app.orgs.models import Org, Repo

logger = logging.getLogger(__name__)

# At most this many gap chats per uploaded document. Only claims the code
# actually contradicts count toward it; the reviewer answers these in a blocking
# modal, so the ceiling is deliberately low.
MAX_GAP_QUESTIONS_PER_DOC = 2
MAX_FILE_CHARS = 20_000  # per-file current-code slice handed to the judge
MAX_CODE_CHARS = 60_000  # total current-code budget across a claim's files

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

CONFLICT_PROMPT = """\
You are checking whether a documented engineering decision still matches the
current code. You are given the CLAIM (from an uploaded legacy document) and the
CURRENT CONTENTS of the files it governs. Decide ONLY whether the current code
CONTRADICTS the claim — do not use any knowledge beyond what is given here.

CLAIM:
{claim}

CURRENT CODE (the files this claim governs):
{code}

## Decide
- "conflicts": true only if the current code clearly reverses, removes, or
  diverges from what the claim asserts. Put the paths whose code contradicts it
  in "files" (a subset of the files shown).
- "conflicts": false if the code still upholds the claim, merely touches related
  areas, or the claim isn't about anything visible in the code shown. When unsure,
  answer false — a false alarm wastes a reviewer's time.

Output ONLY the JSON object, no prose, no fences.

## Output schema
{{"conflicts": true | false, "files": [str], "reasoning": str}}
"""


class _ConflictVerdict(BaseModel):
    """One judge call's read of claim-vs-code."""

    conflicts: bool = False
    files: list[str] = []
    reasoning: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_verdict(text: str | None) -> _ConflictVerdict:
    """Tolerant parse of the judge's JSON object; any misbehavior degrades to a
    no-conflict verdict so a bad response never opens a spurious gap chat."""
    if not text:
        return _ConflictVerdict()
    try:
        data = json.loads(_FENCE_RE.sub("", text.strip()))
        return _ConflictVerdict.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError):
        return _ConflictVerdict()


async def _read_current_code(
    files: list[str], *, get_file, cache: dict[str, str | None]
) -> dict[str, str]:
    """Current contents of a claim's anchored files, via the GitHub toolset,
    bounded per file and in total. Files that error or read empty are dropped —
    the judge only sees code we could actually fetch. Reads are memoized across
    memories that share a file."""
    out: dict[str, str] = {}
    budget = MAX_CODE_CHARS
    for path in files:
        if budget <= 0:
            break
        if path not in cache:
            result = await get_file({"path": path})
            # The toolset returns error strings prefixed "Error:" rather than
            # raising — treat those (and a directory listing) as unreadable.
            cache[path] = None if result.startswith("Error:") else result
        content = cache[path]
        if not content:
            continue
        slice_ = content[: min(MAX_FILE_CHARS, budget)]
        out[path] = slice_
        budget -= len(slice_)
    return out


async def _judge_memory(
    memory: MemoryIndex,
    *,
    org: Org,
    bb: Backboard,
    get_file,
    cache: dict[str, str | None],
) -> _ConflictVerdict:
    """Judge one memory against its anchored files' current code. Never raises:
    unreadable code or a transport error is a no-conflict verdict."""
    code = await _read_current_code(
        memory.anchors.files, get_file=get_file, cache=cache
    )
    if not code:
        return _ConflictVerdict()
    blocks = "\n\n".join(f"### {path}\n{body}" for path, body in code.items())
    claim = re.sub(r"^\[repo:[^\]]*\]\s*", "", memory.contentSnapshot).strip()
    try:
        response = await bb.send_message(
            CONFLICT_PROMPT.format(claim=claim, code=blocks),
            assistant_id=org.bbAssistantId,
            memory="off",
            json_output=True,
            llm_provider=CLOSED_WORLD_LLM_PROVIDER,
            model_name=CLOSED_WORLD_MODEL_NAME,
        )
    except Exception:  # noqa: BLE001 — best-effort; a hiccup means "no gap"
        logger.exception("gap-detection judge call failed for %s", memory.bbMemoryId)
        return _ConflictVerdict()
    verdict = _parse_verdict(final_text(response))
    # Keep only cited paths that were actually among this claim's anchors.
    verdict.files = [f for f in verdict.files if f in code] or list(code)
    return verdict


async def detect_and_open_gaps(
    memories: list[MemoryIndex],
    *,
    org: Org,
    repo: Repo,
    bb: Backboard,
    github: GitHubApp,
) -> int:
    """Open a gap chat for each freshly-enriched ``legacy_doc`` memory whose
    current code contradicts it, up to ``MAX_GAP_QUESTIONS_PER_DOC``.

    Only memories with file anchors are checkable (there's no code to read
    otherwise). Returns the number of gap chats opened. Best-effort: if the repo
    can't be reached at all, returns 0 without raising."""
    candidates = [m for m in memories if m.anchors.files]
    if not candidates:
        return 0

    # Toolset + history bound to this repo; a missing/deactivated installation
    # raises here, same as enrichment — treat as "cannot check" and bail.
    try:
        _, registry = build_github_toolset(org, repo, github)
        head_sha = await build_repo_history(org, repo, github).head_sha()
    except Exception:  # noqa: BLE001 — no repo access means no gap detection
        logger.exception(
            "could not reach %s/%s for gap detection", repo.owner, repo.name
        )
        return 0
    get_file = registry["get_file"]

    cache: dict[str, str | None] = {}
    opened = 0
    unchecked = 0
    for i, memory in enumerate(candidates):
        if opened >= MAX_GAP_QUESTIONS_PER_DOC:
            # Hit the per-doc ceiling — stop judging the rest rather than pay for
            # calls whose gap chats we'd never open.
            unchecked = len(candidates) - i
            break
        verdict = await _judge_memory(
            memory, org=org, bb=bb, get_file=get_file, cache=cache
        )
        if not verdict.conflicts:
            continue
        synthetic = StalenessVerdict(
            status="gap",
            memoryCommitSha=head_sha,
            currentShaCheckedAt=_now_iso(),
            changedFiles=verdict.files,
            commitsSince=None,
            newerMemoryExists=False,
        )
        chat = await open_gap_chat(
            memory, synthetic, org=org, bb=bb, trigger_commit_sha=head_sha
        )
        if chat is not None:
            opened += 1

    if unchecked:
        logger.info(
            "gap detection hit the %d-question cap for %s/%s: %d further memories "
            "left unchecked",
            MAX_GAP_QUESTIONS_PER_DOC,
            repo.owner,
            repo.name,
            unchecked,
        )
    logger.info(
        "gap detection opened %d gap chat(s) for %s/%s", opened, repo.owner, repo.name
    )
    return opened
