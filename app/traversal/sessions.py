"""In-process registry of the most-recent active MCP session per (user, org).

MCP is stateless request/response — there is no long-lived connection to track,
and ``sessionId`` is client-supplied (``X-Session-Id``). So "the user's active
session" is defined as the most recent session id seen on an MCP tool call for
that ``(user_id, org_id)`` pair. The MCP endpoint ``record``s on every call that
carries a session id; a live graph view ``latest``s to pick who to follow and
``watch``es to auto-follow when the same user starts a newer session.

Single-instance only, mirroring ``app.traversal.channel``: state lives in this
process's memory, so a multi-worker deployment won't see a session recorded on
worker A from a view on worker B (a Redis backing is a later ticket — ``record``
and ``watch`` are the seams it would swap). No TTL: a session stays "latest"
until a newer one for the same pair replaces it. Watcher callbacks run
synchronously on the event loop (no threads/locks), same as the channel; a
transport bridges them to its async send.
"""

from collections import defaultdict
from collections.abc import Callable

# Notified with the new session id whenever the latest changes for its pair.
Watcher = Callable[[str], None]


class ActiveSessionRegistry:
    """(user_id, org_id) -> latest session id, with change notification."""

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], str] = {}
        self._watchers: dict[tuple[str, str], set[Watcher]] = defaultdict(set)

    def record(self, user_id: str, org_id: str, session_id: str) -> None:
        """Mark ``session_id`` as this pair's active session. If it changed the
        latest, notify every watcher so a live view can switch to follow it."""
        key = (user_id, org_id)
        if self._latest.get(key) == session_id:
            return  # same session already active — nothing to announce
        self._latest[key] = session_id
        # snapshot: a watcher may unwatch itself while being notified
        for cb in tuple(self._watchers.get(key, ())):
            cb(session_id)

    def latest(self, user_id: str, org_id: str) -> str | None:
        """The most-recent active session id for the pair, or None if none seen."""
        return self._latest.get((user_id, org_id))

    def watch(self, user_id: str, org_id: str, cb: Watcher) -> Callable[[], None]:
        """Register ``cb`` to be called with the new session id whenever this
        pair's latest changes; returns an idempotent unwatch."""
        key = (user_id, org_id)
        self._watchers[key].add(cb)

        def unwatch() -> None:
            subs = self._watchers.get(key)
            if subs is not None:
                subs.discard(cb)
                if not subs:
                    self._watchers.pop(key, None)

        return unwatch


# Process-wide singleton — the MCP endpoint (record side) and the live WS
# transport (latest/watch side) both import this instance.
active_sessions = ActiveSessionRegistry()
