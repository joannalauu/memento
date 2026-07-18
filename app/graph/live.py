"""Live traversal WebSocket with auto-follow (T4.6a).

A browser opens ``WS /orgs/{org_id}/graph/live`` on graph-view load to watch its
own user's active MCP session traverse the memory graph. There is no session
picker: on connect we resolve the caller's most-recent active MCP session for the
org (``active_sessions``), subscribe to it on the T4.4 channel, and relay each
``TraversalEvent``. If the same user starts a newer MCP session we switch the
subscription to follow their latest activity.

Auth reuses the deployed auth plate: cookies ride a WebSocket upgrade like any
HTTP request, and the cookie plates read the token straight off ``request.cookies``
(which a ``WebSocket`` also exposes), so ``get_current_user(websocket)`` just works.

The channel/registry callbacks are synchronous and run inline on the event loop;
they can't ``await`` a socket send, so they enqueue serialized frames onto a
bounded queue that the sender task drains. On overflow we drop the oldest frame —
the client's pacing buffer (T4.7) is the real queue, this one only rides out a
burst. Events are ephemeral (no replay); a reconnecting client passes its last
seen ``seq`` and, if steps were emitted while it was gone, we tell it to refetch
the static graph rather than trying to replay.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import cast

from beanie import PydanticObjectId
from fastapi import APIRouter, WebSocketDisconnect

from app.hackplate.hackplate_types import HackplateRequest, HackplateWebSocket
from app.orgs.crud import get_org
from app.traversal import TraversalEvent, active_sessions, traversal_channel

router = APIRouter()

# Server->client heartbeat cadence and the client-silence budget before we treat
# the socket as dead. A browser that navigated away without a clean close (or a
# wedged proxy) stops answering pings and is reaped once it goes silent.
_PING_INTERVAL_S = 20.0
_CLIENT_TIMEOUT_S = 45.0
# Bounded pacing buffer — see module docstring. On overflow: drop-oldest.
_QUEUE_MAX = 256
# 1008 = policy violation (auth / not a member); the browser sees it on close.
_POLICY_VIOLATION = 1008


def _query_last_seq(websocket: HackplateWebSocket) -> int | None:
    """The ``?lastSeq=`` reconnect hint: the last event seq the client saw."""
    raw = websocket.query_params.get("lastSeq")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@router.websocket("/{org_id}/graph/live")
async def graph_live(websocket: HackplateWebSocket, org_id: str) -> None:
    await websocket.accept()

    # Auth: reuse the deployed auth plate (reads the session cookie from the
    # handshake). Invalid/absent session -> clean policy-violation close. The
    # plate is typed for Request, but only touches `.cookies`/`.app` — which a
    # WebSocket also exposes — so the cast is the duck-typing made explicit.
    try:
        user = await websocket.app.state.config.auth.get_current_user(
            cast(HackplateRequest, websocket)
        )
    except Exception:
        await websocket.close(code=_POLICY_VIOLATION)
        return

    # Org membership — same inline check as the HTTP graph routes.
    try:
        oid = PydanticObjectId(org_id)
    except Exception:
        await websocket.close(code=_POLICY_VIOLATION)
        return
    org = await get_org(oid)
    if org is None or not any(m.userId == user.id for m in org.members):
        await websocket.close(code=_POLICY_VIOLATION)
        return

    user_key, org_key = str(user.id), str(oid)

    # Serialized frames flow through this queue; sync callbacks enqueue, the
    # sender task drains. Drop-oldest keeps a slow client from unbounded growth.
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAX)

    def enqueue(frame: dict) -> None:
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()  # drop oldest
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

    def on_event(ev: TraversalEvent) -> None:
        enqueue({"type": "event", "event": ev.model_dump()})

    # The active channel subscription; swapped when the user starts a newer
    # session. Mutated from callbacks that run inline on this event loop, so no
    # lock is needed (single-threaded asyncio).
    unsub: Callable[[], None] | None = None

    def follow(session_id: str, announce: str) -> None:
        nonlocal unsub
        if unsub is not None:
            unsub()
        unsub = traversal_channel.subscribe(session_id, on_event)
        enqueue(
            {
                "type": announce,
                "sessionId": session_id,
                "seq": traversal_channel.current_seq(session_id),
            }
        )

    def on_new_session(session_id: str) -> None:
        follow(session_id, "switch")

    unwatch = active_sessions.watch(user_key, org_key, on_new_session)

    # Attach to the current session, if the user has one active yet.
    sid = active_sessions.latest(user_key, org_key)
    if sid is not None:
        current = traversal_channel.current_seq(sid)
        follow(sid, "following")
        # Reconnect gap: any step emitted while the client was gone can't be
        # replayed (ephemeral channel), so ask it to refresh the static graph.
        last_seq = _query_last_seq(websocket)
        if last_seq is not None and current > last_seq + 1:
            enqueue({"type": "refresh", "reason": "gap"})
    else:
        enqueue({"type": "waiting"})

    last_client_at = [time.monotonic()]

    async def sender() -> None:
        while True:
            frame = await queue.get()
            await websocket.send_json(frame)

    async def receiver() -> None:
        # Consume client frames (pong / hello) purely to detect liveness and
        # disconnect; content is advisory — the ?lastSeq= query param is the
        # authoritative reconnect hint.
        while True:
            try:
                await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except Exception:
                return
            last_client_at[0] = time.monotonic()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            if time.monotonic() - last_client_at[0] > _CLIENT_TIMEOUT_S:
                return  # dead socket — no client traffic within the budget
            await websocket.send_json({"type": "ping"})

    tasks = [
        asyncio.create_task(sender()),
        asyncio.create_task(receiver()),
        asyncio.create_task(heartbeat()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        unwatch()
        if unsub is not None:
            unsub()
        with contextlib.suppress(Exception):
            await websocket.close()
