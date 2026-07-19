"""Web graph Q&A over SSE (T4.5): ``POST /orgs/{org_id}/graph/ask``.

The asker IS the watcher: one response interleaves the assistant's answer with
the traversal events its graph-tool calls emit, so the browser that asked can
animate its graph without a second connection. Frames (``data: <json>\\n\\n``):

    {"type": "content_delta", "content": ...}
    {"type": "tool_activity", "nodeId": ..., "edgeKind": ..., "kind": ...}
    {"type": "done", "citations": [{"nodeId": ..., "prNumber": ...}]}   terminal
    {"type": "error", "code": ..., "message": ...}                      terminal

Two producers merge into one queue: a pump task draining the Backboard tool
loop (content deltas), and the T4.4 channel subscription for this ask's
session (traversal events). Interleaving is loss-free by construction — the
channel publishes synchronously during tool execution, i.e. while the pump is
suspended inside `stream_with_tools`, so every traversal event is enqueued
before the pump can enqueue its done-sentinel.

The session id is the client's ``X-Session-Id`` when sent (so an already-open
live WS view, T4.6a, follows the same traversal), else minted server-side and
echoed in the ``X-Session-Id`` response header.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import uuid4

from backboard import BackboardAPIError
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.backboard.client import Backboard, get_backboard
from app.backboard.executor import (
    ExecutorError,
    MaxRoundsExceeded,
    ToolFn,
    stream_with_tools,
)
from app.dependencies import get_current_user, get_traversal_tag
from app.github.client import GitHubApp, get_github
from app.graph.qa_tools import CitationCollector, build_qa_toolset
from app.orgs.crud import get_org, list_repos_for_org
from app.orgs.models import Org, Repo, User
from app.traversal import TraversalEvent, TraversalTag, traversal_channel

logger = logging.getLogger(__name__)

router = APIRouter()

ASK_SYSTEM_PROMPT = (
    "You answer questions about this organization's engineering knowledge "
    "graph — the decisions its engineers recorded, and the PRs, files, and "
    "features they connect to. Start with find_entry_points on the user's "
    "question, then walk_graph from the most promising nodeIds; follow "
    "superseded_by edges to check a decision is still current before citing "
    "it. {github_line} Answer concisely, grounded in what the graph returned; "
    "if it has nothing relevant, say so plainly."
)
_GITHUB_AVAILABLE_LINE = (
    "Use the GitHub tools (each takes a 'repo' argument) only when the "
    "question needs current code specifics. Connected repos: {repos}."
)
_GITHUB_UNAVAILABLE_LINE = (
    "This organization has no GitHub connection, so answer from the knowledge "
    "graph alone."
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)


def _system_prompt(org: Org, repos: list[Repo]) -> str:
    if org.githubInstallationId is None or not repos:
        github_line = _GITHUB_UNAVAILABLE_LINE
    else:
        github_line = _GITHUB_AVAILABLE_LINE.format(
            repos=", ".join(f"{r.owner}/{r.name}" for r in repos)
        )
    return ASK_SYSTEM_PROMPT.format(github_line=github_line)


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _ask_stream(
    bb: Backboard,
    question: str,
    *,
    tools: list[dict[str, Any]],
    registry: Mapping[str, ToolFn],
    citations: CitationCollector,
    assistant_id: str,
    session_id: str,
    system_prompt: str,
) -> AsyncIterator[str]:
    """Merge the Backboard tool loop and this session's traversal events into
    one SSE frame stream, ending with exactly one done or error frame."""
    # Unbounded on purpose (unlike live.py's drop-oldest pacing buffer): content
    # deltas must not be dropped, and the producer is finite — the loop is capped
    # at max_rounds and every tool output at MAX_TOOL_OUTPUT_CHARS.
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def on_traversal(event: TraversalEvent) -> None:
        # Sync callback on the event loop (channel contract) — enqueue only.
        queue.put_nowait(("traversal", event))

    unsubscribe = traversal_channel.subscribe(session_id, on_traversal)

    async def pump() -> None:
        try:
            async for event in stream_with_tools(
                bb,
                question,
                tools=tools,
                registry=registry,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                memory="off",
            ):
                queue.put_nowait(("bb", event))
        except MaxRoundsExceeded as exc:
            # Known limitation: the streaming loop doesn't cancel the
            # server-side run here (unlike run_with_tools) — best-effort only.
            queue.put_nowait(("error", ("max_rounds_exceeded", str(exc))))
        except ExecutorError as exc:
            queue.put_nowait(("error", ("executor_error", str(exc))))
        except BackboardAPIError as exc:
            # The SDK raises (never yields) stream error/run_failed events.
            queue.put_nowait(("error", ("backboard_error", str(exc))))
        except asyncio.CancelledError:
            raise  # client disconnected — no frame to send
        except Exception:
            logger.exception("graph ask stream failed (session %s)", session_id)
            queue.put_nowait(("error", ("internal", "internal error")))
        else:
            queue.put_nowait(("done", None))

    task = asyncio.create_task(pump())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "bb":
                if payload.get("type") == "content_streaming":
                    yield _frame(
                        {
                            "type": "content_delta",
                            "content": payload.get("content", ""),
                        }
                    )
                # reasoning_*/tool_submit_required/run_ended are internal.
            elif kind == "traversal":
                yield _frame(
                    {
                        "type": "tool_activity",
                        "nodeId": payload.nodeId,
                        "edgeKind": payload.edgeKind,
                        "kind": payload.kind,
                    }
                )
            elif kind == "error":
                code, message = payload
                yield _frame({"type": "error", "code": code, "message": message})
                return
            else:  # done
                yield _frame({"type": "done", "citations": citations.as_list()})
                return
    finally:
        # Reached on completion, client disconnect (Starlette cancels us →
        # CancelledError at queue.get()), or generator aclose(). Unsubscribe is
        # idempotent; cancelling the pump closes the SDK's HTTP stream.
        unsubscribe()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@router.post("/{org_id}/graph/ask")
async def ask_graph_endpoint(
    org_id: PydanticObjectId,
    body: AskRequest,
    user: User = Depends(get_current_user),
    tag: TraversalTag | None = Depends(get_traversal_tag),
    bb: Backboard = Depends(get_backboard),
    gh: GitHubApp = Depends(get_github),
) -> StreamingResponse:
    """Ask a question about the org's knowledge graph; SSE response interleaves
    the streamed answer with this ask's traversal events. Only a member of the
    org may ask."""
    org: Org | None = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    session_id = tag.session_id if tag is not None else uuid4().hex
    repos = await list_repos_for_org(org_id)
    tools, registry, citations = build_qa_toolset(
        bb=bb, gh=gh, org=org, repos=repos, session_id=session_id
    )
    stream = _ask_stream(
        bb,
        body.question,
        tools=tools,
        registry=registry,
        citations=citations,
        assistant_id=org.bbAssistantId,
        session_id=session_id,
        system_prompt=_system_prompt(org, repos),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
            "X-Session-Id": session_id,
        },
    )
