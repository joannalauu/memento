"""By-interview staleness handling: reconcile a legacy-doc memory with the code.

When a merge touches files a ``legacy_doc`` memory anchors and `staleness_check`
comes back non-fresh, `open_gap_chat` asks one question — "the old docs say X
about this area, is that still accurate?" — on a Backboard thread. The engineer's
answer is classified closed-world (`classify_answer`) into one of two outcomes:

- ``verified`` — the claim still holds. `_verify_memory` flips the memory to
  ``verified`` confidence AND re-baselines its ``commitSha`` to the triggering
  commit, so it stops re-flagging as stale on this same code.
- ``superseded`` — the code moved past it. `_supersede_memory` writes a new,
  verified memory carrying the corrected statement (same anchors, new baseline),
  deletes the old one from Backboard's store, and records the lineage
  (``supersededBy``) on the now-retired index doc.

Legacy knowledge is thus refreshed lazily, exactly where the code is changing,
on the interview path that already exists — no new sweep or scheduler. Only
``legacy_doc`` memories are eligible; everything degrades gracefully (a
model-classification failure leaves the chat open rather than mutating a memory
on a guess).
"""

import json
import logging
import re
from datetime import datetime, timezone

from pydantic import ValidationError

from app.backboard.client import Backboard
from app.backboard.executor import final_text
from app.backboard.models import MemoryIndex
from app.context_engine.schemas import StalenessVerdict
from app.gap_chat import crud
from app.gap_chat.models import GapChat
from app.gap_chat.schemas import GapClassification
from app.orgs.models import Org

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

CLASSIFY_PROMPT = """\
You are reconciling one legacy documentation claim with the current codebase. An
engineer was asked whether the claim still holds and has answered. Decide the
outcome — do NOT use any knowledge beyond what is given here.

LEGACY CLAIM (from an uploaded doc):
{claim}

CODE AREA THAT RECENTLY CHANGED (files):
{files}

QUESTION ASKED:
{question}

ENGINEER'S ANSWER:
{answer}

## Decide
- "verified": the answer confirms the claim is STILL accurate for the current
  code. Leave `statement` null.
- "superseded": the answer says the claim is now wrong or has changed. Put the
  corrected, self-contained decision (one sentence, understandable without this
  conversation) in `statement`.

When the answer is ambiguous, prefer "superseded" only if it clearly states a
change; otherwise "verified". Output ONLY the JSON object, no prose, no fences.

## Output schema
{{"resolution": "verified" | "superseded", "statement": str | null,
  "reasoning": str}}
"""


def build_question(memory_content: str, changed_files: list[str]) -> str:
    """The one verification question. Templated (no model call) so opening a
    chat is deterministic and free — the chat with the model happens on answer."""
    stripped = re.sub(r"^\[repo:[^\]]*\]\s*", "", memory_content).strip()
    where = ", ".join(changed_files[:5]) if changed_files else "this area"
    return (
        f'The uploaded docs say: "{stripped}"\n\n'
        f"The code in {where} has changed since that was written. "
        "Is it still accurate? If not, what's true now?"
    )


async def open_gap_chat(
    memory: MemoryIndex,
    verdict: StalenessVerdict,
    *,
    org: Org,
    bb: Backboard,
    trigger_commit_sha: str,
    pr_number: int | None = None,
) -> GapChat | None:
    """Open (or return the existing) gap chat for a stale ``legacy_doc`` memory.

    Returns None when the memory isn't eligible (not legacy_doc, already
    verified, or the verdict is fresh) — callers can pass any flagged memory and
    let this decide. Best-effort on Backboard: a thread-creation failure still
    opens the chat (the thread is created lazily on first answer)."""
    if memory.source != "legacy_doc" or memory.confidence == "verified":
        return None
    if verdict.status not in ("stale", "gap"):
        return None
    assert memory.id is not None

    existing = await crud.get_open_chat_for_memory(memory.bbMemoryId)
    if existing is not None:
        return existing

    thread_id: str | None = None
    try:
        thread = await bb.create_thread(org.bbAssistantId)
        thread_id = str(thread.id)
    except Exception:  # noqa: BLE001 — the thread is convenience, not required
        logger.warning(
            "could not open Backboard thread for gap chat on %s", memory.bbMemoryId
        )

    question = build_question(memory.contentSnapshot, verdict.changedFiles)
    return await crud.create_gap_chat(
        org_id=memory.orgId,
        repo_id=memory.repoId,
        bb_memory_id=memory.bbMemoryId,
        memory_content=memory.contentSnapshot,
        trigger_commit_sha=trigger_commit_sha,
        trigger_status=verdict.status,
        changed_files=verdict.changedFiles,
        pr_number=pr_number,
        question=question,
        bb_thread_id=thread_id,
    )


def _parse_classification(text: str | None) -> GapClassification | None:
    if not text:
        return None
    try:
        return GapClassification.model_validate(
            json.loads(_FENCE_RE.sub("", text.strip()))
        )
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


async def classify_answer(
    chat: GapChat, answer: str, *, bb: Backboard, assistant_id: str
) -> GapClassification | None:
    """Closed-world (memory="off") read of the answer. Returns None on model
    misbehavior so the caller can leave the chat open instead of guessing."""
    question = chat.messages[0].text if chat.messages else ""
    prompt = CLASSIFY_PROMPT.format(
        claim=re.sub(r"^\[repo:[^\]]*\]\s*", "", chat.memoryContent).strip(),
        files=", ".join(chat.changedFiles) or "(unknown)",
        question=question,
        answer=answer,
    )
    try:
        response = await bb.send_message(
            prompt,
            assistant_id=assistant_id,
            thread_id=chat.bbThreadId,
            memory="off",
            json_output=True,
        )
    except Exception:  # noqa: BLE001 — transport hiccup shouldn't 500 the answer
        logger.exception("gap-chat classification call failed for %s", chat.id)
        return None
    return _parse_classification(final_text(response))


async def _verify_memory(memory: MemoryIndex, chat: GapChat) -> None:
    """Confirm the memory: upgrade to verified and re-baseline its commit so the
    same code change stops flagging it stale."""
    memory.confidence = "verified"
    memory.commitSha = chat.triggerCommitSha
    memory.stalenessStatus = "fresh"
    memory.stalenessCheckedAt = datetime.now(timezone.utc)
    await memory.save()


async def _supersede_memory(
    memory: MemoryIndex,
    chat: GapChat,
    statement: str,
    *,
    org: Org,
    bb: Backboard,
    author_user_id,
) -> str:
    """Replace the memory with a corrected, verified one. Writes the new memory,
    removes the stale one from Backboard's store, and stamps the lineage onto the
    retired index doc. Returns the new memory's bbMemoryId."""
    new_index = await bb.add_memory(
        assistant_id=org.bbAssistantId,
        org_id=memory.orgId,
        repo_id=memory.repoId,
        repo=memory.anchors.repo,
        content=statement,
        metadata={
            "source": "legacy_doc",
            "confidence": "verified",
            "supersedes": memory.bbMemoryId,
        },
        source="legacy_doc",
        confidence="verified",
        feature=memory.feature,
        commit_sha=chat.triggerCommitSha,
        author_user_id=author_user_id,
        files=memory.anchors.files,
        symbols=memory.anchors.symbols,
    )
    # Drop the stale memory from Backboard's semantic store so it can't resurface;
    # keep the retired index doc for lineage (supersededBy + archived content).
    try:
        await bb.sdk.delete_memory(org.bbAssistantId, memory.bbMemoryId)
    except Exception:  # noqa: BLE001 — lineage below is the load-bearing record
        logger.warning(
            "could not delete superseded memory %s from Backboard", memory.bbMemoryId
        )
    memory.supersededBy = new_index.id
    memory.archivedContent = memory.contentSnapshot
    memory.deletedAt = datetime.now(timezone.utc)
    await memory.save()
    return new_index.bbMemoryId


async def submit_answer(
    chat: GapChat,
    answer: str,
    *,
    org: Org,
    bb: Backboard,
    author_user_id=None,
) -> GapChat:
    """Record the answer and apply its outcome to the memory.

    Appends the user turn, classifies, then verifies or supersedes the memory and
    closes the chat with a summary turn. If classification fails or the memory is
    gone, the chat stays open (answer recorded) so it can be retried."""
    await crud.append_message(chat, "user", answer)

    classification = await classify_answer(
        chat, answer, bb=bb, assistant_id=org.bbAssistantId
    )
    if classification is None:
        await crud.append_message(
            chat,
            "assistant",
            "I couldn't determine whether the claim still holds — leaving this "
            "open. Please try rephrasing.",
        )
        return chat

    memory = await MemoryIndex.find_one({"bbMemoryId": chat.bbMemoryId})
    if memory is None or memory.deletedAt is not None:
        # The memory vanished (deleted/already superseded) between open and
        # answer — nothing to mutate; close as dismissed.
        chat.status = "dismissed"
        chat.resolvedByUserId = author_user_id
        chat.resolvedAt = datetime.now(timezone.utc)
        await crud.append_message(
            chat, "assistant", "This memory is no longer active; closing."
        )
        return chat

    if classification.resolution == "verified":
        await _verify_memory(memory, chat)
        chat.status = "verified"
        summary = "Thanks — confirmed the docs still hold. Marked as verified."
    else:
        statement = (classification.statement or "").strip() or answer.strip()
        new_id = await _supersede_memory(
            memory, chat, statement, org=org, bb=bb, author_user_id=author_user_id
        )
        chat.status = "superseded"
        chat.supersededByMemoryId = new_id
        summary = "Thanks — recorded the update and superseded the old memory."

    chat.resolvedByUserId = author_user_id
    chat.resolvedAt = datetime.now(timezone.utc)
    await crud.append_message(chat, "assistant", summary)
    return chat


class TranscriptionError(RuntimeError):
    """STT could not produce a usable transcript for a voice answer — raised
    before anything is mutated, so the chat is safe to retry."""


async def ensure_thread(chat: GapChat, *, org: Org, bb: Backboard) -> str | None:
    """The chat's Backboard thread, creating and persisting one if the chat
    opened without it (thread creation is best-effort at open time). Returns None
    if a thread still can't be created."""
    if chat.bbThreadId:
        return chat.bbThreadId
    try:
        thread = await bb.create_thread(org.bbAssistantId)
    except Exception:  # noqa: BLE001 — surfaced to the caller as "no thread"
        logger.warning("could not create Backboard thread for gap chat %s", chat.id)
        return None
    chat.bbThreadId = str(thread.id)
    await chat.save()
    return chat.bbThreadId


async def submit_audio_answer(
    chat: GapChat,
    audio_path: str,
    *,
    org: Org,
    bb: Backboard,
    author_user_id=None,
) -> tuple[GapChat, str]:
    """Answer by voice: transcribe the audio (ElevenLabs STT via Backboard), then
    run the transcript through the exact same verify/supersede flow as a typed
    answer. Returns ``(chat, transcript)``.

    Raises `TranscriptionError` when no thread is available for STT or the audio
    yields no text — the memory is left untouched so the answer can be retried."""
    thread_id = await ensure_thread(chat, org=org, bb=bb)
    if thread_id is None:
        raise TranscriptionError("no Backboard thread available for transcription")
    transcript = await bb.transcribe_audio(thread_id=thread_id, audio_path=audio_path)
    if not transcript.strip():
        raise TranscriptionError("no speech detected in the audio")
    chat = await submit_answer(
        chat, transcript, org=org, bb=bb, author_user_id=author_user_id
    )
    return chat, transcript
