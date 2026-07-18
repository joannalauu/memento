"""Transcript normalizer: mechanical, type-driven filtering of Claude Code JSONL.

Raw transcripts are mostly tool noise (file reads, bash output, diffs). The
signal — engineer prompts, Claude's plans/explanations, subagent reports — is
structurally labeled, so this is a filter, not a summarizer: entries are kept
or dropped by their type, never by their meaning. The pure core
(`normalize_jsonl`) is I/O-free; `normalize_session` drives it against GridFS
and commits `normalizedRef`/`status="normalized"` on the AgentSession.

Filter rules:
- user text (string content or `text` blocks)      → keep
- assistant `text` blocks                          → keep
- assistant `thinking` blocks                      → drop
- assistant `tool_use` blocks                      → one-line stub
- `tool_result` blocks                             → by producing tool:
  Task/Agent (subagent's final report) kept in full; everything else
  (Read/Bash/Grep/... bulk output) dropped
- `summary` lines                                  → keep
- isMeta / isSidechain lines, unknown types,
  malformed lines, harness command wrappers        → drop, never raise

Output is bounded by a token budget (chars/4 heuristic): oversized blocks are
middle-elided, and monster sessions get an entry-level head+tail elision that
always preserves the first user prompt and the final exchanges.
"""

import json
import logging
import re
from typing import Literal

from beanie import PydanticObjectId
from bson import ObjectId
from bson.errors import InvalidId
from gridfs.errors import NoFile
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase

from app.claude_hook import crud
from app.claude_hook.models import AgentSession

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # same advisory heuristic the ingest hook uses
DEFAULT_BUDGET_TOKENS = 30_000
BLOCK_CHAR_CAP = 4_000  # per kept text block (~1k tokens)
STUB_CHAR_CAP = 120  # per tool stub line
HEAD_BUDGET_FRACTION = 0.25  # head share of the budget when eliding
_ELISION_RESERVE_CHARS = 100  # kept back for the elision marker itself

# tool_result blocks are dropped *by the tool that produced them*: bulk-output
# tools (Read, Bash, Grep, ...) are noise, but a Task/Agent result is the
# subagent's final report — real signal, kept in full.
RESULT_KEEP_TOOLS = {"Task", "Agent"}

# tool name → ordered candidate input keys for the one-line stub
_STUB_KEYS: dict[str, tuple[str, ...]] = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path", "file_path"),
    "Bash": ("description", "command"),
    "Grep": ("pattern",),
    "Glob": ("pattern",),
    "Task": ("description",),
    "Agent": ("description",),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
}

# User lines whose text is only harness plumbing (slash-command echoes, local
# command output) sometimes lack isMeta; strip the wrappers and drop if empty.
_HARNESS_TAG_RE = re.compile(
    r"<(command-name|command-message|command-args|"
    r"local-command-stdout|local-command-caveat)>.*?</\1>",
    re.DOTALL,
)


class NormalizedEntry(BaseModel):
    role: Literal["user", "assistant", "system"]
    kind: Literal["text", "tool", "tool_result", "summary", "elision"]
    text: str


class NormalizedResult(BaseModel):
    entries: list[NormalizedEntry]
    token_estimate: int  # rendered chars // CHARS_PER_TOKEN
    truncated: bool  # any block cap or budget elision applied
    source_lines: int
    kept_entries: int


def tool_stub(name: str, tool_input: object) -> str:
    """One-line stub for a tool_use block: `Name: salient-input`, hard-capped.

    The salient value is the first present non-empty string among the tool's
    candidate keys (first line only, for multi-line Bash commands). Unknown
    tools and non-string values fall back to the bare tool name."""
    salient = None
    if isinstance(tool_input, dict):
        for key in _STUB_KEYS.get(name, ()):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                salient = value.strip().splitlines()[0].strip()
                break
    stub = f"{name}: {salient}" if salient else name
    return stub[:STUB_CHAR_CAP]


def _keep_user_text(text: str) -> bool:
    return bool(_HARNESS_TAG_RE.sub("", text).strip())


def _result_text(content: object) -> str:
    """Extract the text of a tool_result's content (string or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return "\n".join(parts).strip()
    return ""


def _user_entries(obj: dict, tool_names: dict[str, str]) -> list[NormalizedEntry]:
    if obj.get("isMeta") or obj.get("isSidechain"):
        return []
    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    out: list[NormalizedEntry] = []
    if isinstance(content, str):
        if _keep_user_text(content):
            out.append(NormalizedEntry(role="user", kind="text", text=content.strip()))
        return out
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and _keep_user_text(text):
                out.append(NormalizedEntry(role="user", kind="text", text=text.strip()))
        elif btype == "tool_result":
            tid = block.get("tool_use_id")
            producer = tool_names.get(tid) if isinstance(tid, str) else None
            if producer in RESULT_KEEP_TOOLS:
                text = _result_text(block.get("content"))
                if text:
                    out.append(
                        NormalizedEntry(role="user", kind="tool_result", text=text)
                    )
    return out


def _assistant_entries(obj: dict, tool_names: dict[str, str]) -> list[NormalizedEntry]:
    if obj.get("isSidechain"):
        return []
    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    out: list[NormalizedEntry] = []
    if isinstance(content, str):
        if content.strip():
            out.append(
                NormalizedEntry(role="assistant", kind="text", text=content.strip())
            )
        return out
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                out.append(
                    NormalizedEntry(role="assistant", kind="text", text=text.strip())
                )
        elif btype == "tool_use":
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            tid = block.get("id")
            if isinstance(tid, str):
                tool_names[tid] = name
            out.append(
                NormalizedEntry(
                    role="assistant",
                    kind="tool",
                    text=tool_stub(name, block.get("input")),
                )
            )
    return out


def _mid_elide(text: str, cap: int) -> str:
    head = int(cap * 0.6)
    tail = cap - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n…[truncated ~{omitted} chars]…\n{text[-tail:]}"


def _render_lines(entries: list[NormalizedEntry]) -> list[str]:
    return [
        json.dumps(e.model_dump(), separators=(",", ":"), ensure_ascii=False)
        for e in entries
    ]


def render_jsonl(entries: list[NormalizedEntry]) -> bytes:
    """Render entries as compact UTF-8 JSONL, one object per line."""
    lines = _render_lines(entries)
    return ("\n".join(lines) + "\n").encode() if lines else b""


def _apply_budget(
    entries: list[NormalizedEntry], budget_chars: int
) -> tuple[list[NormalizedEntry], bool]:
    """Entry-level head+tail elision when the rendered output exceeds budget.

    The head always contains everything through the first user prompt (or the
    leading summaries when there is none) and greedily extends up to its budget
    share; the tail always contains at least the final entry. The elided middle
    is replaced by a single marker entry."""
    sizes = [len(line) + 1 for line in _render_lines(entries)]
    if sum(sizes) <= budget_chars:
        return entries, False

    first_user = next(
        (i for i, e in enumerate(entries) if e.role == "user" and e.kind == "text"),
        None,
    )
    if first_user is not None:
        head_end = first_user + 1
    else:
        head_end = 0
        while head_end < len(entries) and entries[head_end].kind == "summary":
            head_end += 1
    head_budget = int(budget_chars * HEAD_BUDGET_FRACTION)
    head_chars = sum(sizes[:head_end])
    while head_end < len(entries) and head_chars + sizes[head_end] <= head_budget:
        head_chars += sizes[head_end]
        head_end += 1

    tail_budget = budget_chars - head_chars - _ELISION_RESERVE_CHARS
    tail_start = len(entries)
    tail_chars = 0
    while tail_start > head_end:
        nxt = sizes[tail_start - 1]
        # the final entry survives unconditionally; earlier ones must fit
        if tail_start != len(entries) and tail_chars + nxt > tail_budget:
            break
        tail_chars += nxt
        tail_start -= 1

    if tail_start <= head_end:
        return entries, False  # head and tail meet; nothing left to elide

    elided_tokens = sum(sizes[head_end:tail_start]) // CHARS_PER_TOKEN
    marker = NormalizedEntry(
        role="system",
        kind="elision",
        text=(
            f"[elided {tail_start - head_end} entries, "
            f"~{elided_tokens} tokens of mid-session activity]"
        ),
    )
    return entries[:head_end] + [marker] + entries[tail_start:], True


def normalize_jsonl(
    raw: bytes, *, budget_tokens: int = DEFAULT_BUDGET_TOKENS
) -> NormalizedResult:
    """Filter a raw Claude Code JSONL transcript down to its signal.

    Deterministic: the same input and budget always yield identical entries.
    Malformed lines and unknown entry types are skipped, never raised on."""
    entries: list[NormalizedEntry] = []
    tool_names: dict[str, str] = {}
    source_lines = 0
    for line in raw.splitlines():
        source_lines += 1
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")
        if obj_type == "user":
            entries.extend(_user_entries(obj, tool_names))
        elif obj_type == "assistant":
            entries.extend(_assistant_entries(obj, tool_names))
        elif obj_type == "summary":
            summary = obj.get("summary")
            if isinstance(summary, str) and summary.strip():
                entries.append(
                    NormalizedEntry(role="system", kind="summary", text=summary.strip())
                )
        # anything else (system, file-history-snapshot, unknown) is noise

    truncated = False
    capped: list[NormalizedEntry] = []
    for entry in entries:
        # tool_result (Task reports) is exempt: kept whole, bounded only by
        # the global budget below
        if entry.kind in ("text", "summary") and len(entry.text) > BLOCK_CHAR_CAP:
            entry = entry.model_copy(
                update={"text": _mid_elide(entry.text, BLOCK_CHAR_CAP)}
            )
            truncated = True
        capped.append(entry)

    final, elided = _apply_budget(capped, budget_tokens * CHARS_PER_TOKEN)
    return NormalizedResult(
        entries=final,
        token_estimate=len(render_jsonl(final)) // CHARS_PER_TOKEN,
        truncated=truncated or elided,
        source_lines=source_lines,
        kept_entries=len(final),
    )


# --- async driver -----------------------------------------------------------


async def _download_transcript(db: AsyncDatabase, ref: str) -> bytes | None:
    """Fetch a transcript blob; None (logged) on a bad id or missing file."""
    try:
        oid = ObjectId(ref)
    except (InvalidId, TypeError):
        logger.warning("transcript ref %r is not a valid ObjectId", ref)
        return None
    try:
        stream = await crud._transcript_bucket(db).open_download_stream(oid)
        return await stream.read()
    except NoFile:
        logger.warning("transcript blob %s missing from GridFS", ref)
        return None


async def _commit_normalized(
    doc_id: PydanticObjectId,
    expected_transcript_ref: str,
    normalized_ref: str,
    token_estimate: int,
) -> bool:
    """Atomically claim the doc, guarding against a concurrent re-ingest.

    The filter pins transcriptRef/status, so if a resumed session replaced the
    capture mid-normalization the update matches nothing and we lose cleanly.

    Goes straight through the pymongo collection rather than Beanie's
    find_one().update() chain: we're already passing a plain filter/$set, and
    the chain's return type (UpdateQuery) omits __await__ even though the
    concrete object it returns at runtime supports it."""
    result = await AgentSession.get_pymongo_collection().update_one(
        {
            "_id": doc_id,
            "transcriptRef": expected_transcript_ref,
            "status": "stored",
        },
        {
            "$set": {
                "normalizedRef": normalized_ref,
                "status": "normalized",
                "normalizedTokenEstimate": token_estimate,
            }
        },
    )
    return result.modified_count == 1


async def normalize_session(db: AsyncDatabase, agent_session_id: str) -> None:
    """Normalize a stored AgentSession's transcript and commit the result.

    No-ops (logged) when the doc is gone, already past "stored", or its blob is
    missing — status stays "stored" in the retryable cases. On a lost race with
    a re-ingest the freshly uploaded normalized blob is GC'd; the re-ingest has
    already re-enqueued normalization of the fuller transcript."""
    doc = await AgentSession.get(PydanticObjectId(agent_session_id))
    if doc is None:
        logger.info("agentSession %s vanished before normalization", agent_session_id)
        return
    if doc.status != "stored" or doc.normalizedRef is not None:
        logger.info(
            "agentSession %s already %s; skipping normalization",
            agent_session_id,
            doc.status,
        )
        return

    expected_ref = doc.transcriptRef
    raw = await _download_transcript(db, expected_ref)
    if raw is None:
        return

    result = normalize_jsonl(raw)
    blob = render_jsonl(result.entries)
    new_ref = await crud.upload_normalized(
        db, doc.sessionId, blob, source_ref=expected_ref
    )
    try:
        assert doc.id is not None
        claimed = await _commit_normalized(
            doc.id, expected_ref, new_ref, result.token_estimate
        )
    except Exception:
        await crud.delete_transcript(db, new_ref)
        raise
    if not claimed:
        await crud.delete_transcript(db, new_ref)
