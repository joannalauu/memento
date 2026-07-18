/**
 * Wire contract for the live traversal channel (`WS /orgs/{orgId}/graph/live`).
 *
 * Mirrors the backend in `app/traversal/schemas.py` (the `TraversalEvent`) and
 * the server->client frame envelope in `app/graph/live.py`. Rendering/pacing
 * (T4.6/T4.7) consumes {@link TraversalEvent}s; this module only models the
 * transport, not how a step is drawn.
 */

/** `entry` = the agent landed here (no incoming edge); `hop` = it followed one. */
export type TraversalEventKind = "entry" | "hop"

/** Which surface drove the traversal. */
export type TraversalSource = "mcp" | "web"

/** One step of a session's memory traversal. */
export interface TraversalEvent {
  /** Routing key — the MCP/web session this step belongs to. */
  sessionId: string
  /** Monotonic per-session ordinal assigned by the channel. */
  seq: number
  kind: TraversalEventKind
  /** The node landed on (entry) or reached (hop). */
  nodeId: string
  /** Edge kind for hops; null for entries. */
  edgeKind: string | null
  /** Origin node for hops; null for entries. */
  fromNodeId: string | null
  source: TraversalSource
  /** ISO-8601 UTC. */
  timestamp: string
}

/**
 * Server -> client frames. Control frames drive connection state; `event`
 * carries a traversal step.
 */
export type LiveFrame =
  /** Attached to a session; `seq` is the channel's current position. */
  | { type: "following"; sessionId: string; seq: number }
  /** No active MCP session for this user/org yet — the socket holds open. */
  | { type: "waiting" }
  /** The followed user started a newer session; now following it. */
  | { type: "switch"; sessionId: string; seq: number }
  /** A traversal step. */
  | { type: "event"; event: TraversalEvent }
  /** Missed too much to reconcile — client should refetch the static graph. */
  | { type: "refresh"; reason: string }
  /** Heartbeat; client answers with a `pong`. */
  | { type: "ping" }

/** Client -> server frames. */
export type ClientFrame =
  | { type: "pong" }
  /** Reconnect hint (also sent as the `?lastSeq=` query param). */
  | { type: "hello"; lastSeq: number | null }

/** Connection status surfaced by {@link useLiveTraversal}. */
export type LiveStatus = "connecting" | "following" | "waiting" | "closed"
