"""Anchor enrichment: legacy docs -> anchor-searchable decision memories.

An uploaded document lands in Backboard's RAG store for narrative Q&A, but a RAG
document can't be reached by the context engine's anchor-based retrieval, which
keys on the files/symbols a memory governs (see app/context_engine). This module
closes that gap: for each ingested doc it asks the assistant to pull out the
doc's decision-like claims and infer which repo files/modules each concerns, then
writes every claim as its own repo-scoped memory tagged
``{source: "legacy_doc", doc: <filename>, confidence: "unverified"}``. The full
doc stays in RAG; its individual decisions become first-class, anchor-searchable
memories alongside interview- and session-derived ones.

Inference is grounded, not free-form: a claim's file anchors are validated
against the repo's actual tree (``list_tree``), so a hallucinated path is dropped
rather than written as a dead anchor. Extraction runs closed-world
(``memory="off"``, ``json_output``) like the consistency judge — the model sees
only the doc text and the skeleton, never the assistant's own memory. Every
failure mode (bad JSON, a Backboard/GitHub error, a claim with no valid anchor)
degrades to fewer memories, never an exception: enrichment is best-effort
augmentation and the doc is already safely in RAG regardless.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from app.backboard.client import Backboard
from app.backboard.executor import final_text
from app.backboard.models import MemoryIndex
from app.file_upload.gap_detection import detect_and_open_gaps
from app.file_upload.models import DocumentIndexEntry
from app.github.client import GitHubApp
from app.github.tools import build_github_toolset
from app.orgs.models import Org, Repo

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 100_000  # doc text handed to the extractor, bounded for the prompt
MAX_CLAIMS = 40  # ceiling on memories written per doc

CLAIM_EXTRACTION_PROMPT = """\
You are extracting durable engineering DECISIONS from a legacy document so they
can be indexed against the code they govern.

You are given (1) the document text and (2) the repository file tree. Return the
document's decision-like claims: statements that record a choice, constraint,
convention, or rationale that governs how the code is or must be built — e.g.
"auth tokens are validated in middleware, never per-route" or "we use asyncpg,
not psycopg2". Ignore narrative, background, status updates, and anything that is
not a durable decision.

For each claim, infer which files/modules it concerns by matching against the
repository tree. Use ONLY paths that appear verbatim in the tree — never invent a
path. If a claim concerns code you cannot locate a path for, return it with an
empty "files" list rather than guessing. Optionally list the code symbols
(function/class names) the claim names.

## Document

{doc}

## Repository tree

{skeleton}

## Rules
- Output ONLY a JSON array, no preamble, no markdown fences.
- Each element: {{"claim": str, "files": [str], "symbols": [str]}}.
- "claim" is one self-contained sentence understandable without the document.
- Every path in "files" MUST appear exactly in the repository tree above.
- Return [] if the document records no durable decisions.

## Output schema
[{{"claim": str, "files": [str], "symbols": [str]}}]
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")
# `list_tree` lines: "app/main.py  (blob, 123 B)" / "app/foo  (tree)".
_TREE_LINE_RE = re.compile(r"^(.*?)\s+\((blob|tree)(?:,.*)?\)$")


class DecisionClaim(BaseModel):
    """One decision the doc records, plus the code it governs."""

    claim: str
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


def _skeleton_files(list_tree_output: str) -> set[str]:
    """The set of blob (file) paths in a ``list_tree`` listing. Directory (tree)
    lines are excluded: an anchor must be a real file path so it can match a
    diff's file anchors downstream."""
    files: set[str] = set()
    for line in list_tree_output.splitlines():
        m = _TREE_LINE_RE.match(line.strip())
        if m and m.group(2) == "blob":
            files.add(m.group(1).strip())
    return files


def _parse_claims(text: str | None) -> list[DecisionClaim]:
    """Tolerant parse of the extractor's JSON array. Any malformed element is
    skipped and a non-array/unparseable response yields []; never raises."""
    if not text:
        return []
    try:
        data = json.loads(_FENCE_RE.sub("", text.strip()))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    claims: list[DecisionClaim] = []
    for item in data:
        try:
            claim = DecisionClaim.model_validate(item)
        except ValidationError:
            continue
        if claim.claim.strip():
            claims.append(claim)
    return claims


async def extract_decision_claims(
    doc_text: str,
    skeleton: str,
    *,
    bb: Backboard,
    assistant_id: str,
    valid_files: set[str],
    model_name: str | None = None,
) -> list[DecisionClaim]:
    """One closed-world call: doc + repo tree -> grounded decision claims.

    File anchors are filtered to paths that actually exist in the tree, so a
    hallucinated path is dropped rather than written as a dead anchor. Never
    raises — a Backboard transport error is logged and yields []."""
    prompt = CLAIM_EXTRACTION_PROMPT.format(
        doc=doc_text[:MAX_DOC_CHARS], skeleton=skeleton
    )
    try:
        response = await bb.send_message(
            prompt,
            assistant_id=assistant_id,
            memory="off",
            json_output=True,
            model_name=model_name,
        )
    except Exception:  # noqa: BLE001 — extraction is best-effort, must not raise
        logger.exception("legacy-doc claim extraction call failed")
        return []
    claims = _parse_claims(final_text(response))
    for claim in claims:
        claim.files = [f for f in claim.files if f in valid_files]
    return claims[:MAX_CLAIMS]


async def enrich_document(
    entry: DocumentIndexEntry,
    *,
    doc_text: str,
    org: Org,
    repo: Repo,
    bb: Backboard,
    github: GitHubApp,
    model_name: str | None = None,
) -> list[MemoryIndex]:
    """Extract the doc's decisions and write each as a ``legacy_doc`` memory.

    Grounds file anchors in the repo tree, tags every memory with its source
    doc, and mirrors into ``memoryIndex`` via ``add_memory`` so the decisions are
    anchor-searchable. Returns the memories written; a single claim's write
    failure is logged and skipped, never aborting the rest. Best-effort: any
    upstream failure (no repo tree, no decisions) returns [] without raising."""
    if not doc_text.strip():
        logger.info("enrichment skipped for %s: no extractable text", entry.filename)
        return []
    repo_full = f"{repo.owner}/{repo.name}"

    # Repo skeleton for grounding. build_github_toolset raises if the org has no
    # installation or the repo is deactivated — treat as "cannot ground" and bail.
    try:
        _, registry = build_github_toolset(org, repo, github)
        skeleton = await registry["list_tree"]({})
    except Exception:  # noqa: BLE001 — no tree means no grounding; skip, don't raise
        logger.exception("could not fetch repo tree for %s", repo_full)
        return []
    valid_files = _skeleton_files(skeleton)

    claims = await extract_decision_claims(
        doc_text,
        skeleton,
        bb=bb,
        assistant_id=org.bbAssistantId,
        valid_files=valid_files,
        model_name=model_name,
    )
    if not claims:
        logger.info("no decisions extracted from %s", entry.filename)
        return []

    assert org.id is not None and repo.id is not None
    # Ticket's literal tag, carried in Backboard metadata so the memory is
    # self-describing; source/confidence are also persisted structurally on the
    # memoryIndex mirror by add_memory.
    written: list[MemoryIndex] = []
    for claim in claims:
        try:
            index = await bb.add_memory(
                assistant_id=org.bbAssistantId,
                org_id=org.id,
                repo_id=repo.id,
                repo=repo_full,
                content=claim.claim,
                metadata={
                    "source": "legacy_doc",
                    "doc": entry.filename,
                    "confidence": "unverified",
                    "documentId": str(entry.id),
                },
                source="legacy_doc",
                confidence="unverified",
                files=claim.files,
                symbols=claim.symbols,
            )
        except Exception:  # noqa: BLE001 — one bad write must not lose the rest
            logger.exception("failed writing legacy_doc memory for %s", entry.filename)
            continue
        written.append(index)
    logger.info("enriched %s: wrote %d decision memories", entry.filename, len(written))
    return written


async def run_document_enrichment(
    entry: DocumentIndexEntry,
    *,
    doc_text: str,
    org: Org,
    repo: Repo,
    bb: Backboard,
    github: GitHubApp,
) -> None:
    """BackgroundTasks seam for anchor enrichment — the single place to swap in a
    real queue/worker later. Failures are logged and swallowed: the doc is
    already in RAG, so a failed enrichment just means no anchor memories yet.

    After the doc's decisions are written, `detect_and_open_gaps` checks each
    against the current code and opens a gap chat where they already disagree, so
    the reviewer is asked to reconcile stale claims right after upload."""
    try:
        written = await enrich_document(
            entry, doc_text=doc_text, org=org, repo=repo, bb=bb, github=github
        )
    except Exception:  # noqa: BLE001 — background task; nothing to surface to
        logger.exception("enrichment failed for document %s", entry.id)
        return
    try:
        await detect_and_open_gaps(written, org=org, repo=repo, bb=bb, github=github)
    except Exception:  # noqa: BLE001 — gap detection is best-effort augmentation
        logger.exception("gap detection failed for document %s", entry.id)
