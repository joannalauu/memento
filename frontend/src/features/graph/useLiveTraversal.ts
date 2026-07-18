/**
 * `useLiveTraversal` — the connection layer for the live traversal channel
 * (T4.6a). Opens `WS /orgs/{orgId}/graph/live` on graph-view load, relays
 * `TraversalEvent`s to `onEvent`, and closes on unmount / org change.
 *
 * Scope: connection lifecycle only — auto-follow is handled server-side, and
 * highlighting/pacing (T4.6/T4.7) lives in the consumer. This hook does not
 * touch `GraphView`; it hands each event to `onEvent` and signals `onRefresh`
 * when the socket says the client missed too much to reconcile.
 *
 * Auth is the session cookie, which rides the WS handshake automatically — no
 * token is handled in JS, consistent with the fetch layer (`lib/api/http.ts`).
 */
import { useCallback, useEffect, useRef, useState } from "react"

import { WS_BASE_URL } from "@/lib/api/config"

import type {
  ClientFrame,
  LiveFrame,
  LiveStatus,
  TraversalEvent,
} from "./live-types"

export interface UseLiveTraversalOptions {
  /** Gate the connection (e.g. only while the graph view is mounted). */
  enabled?: boolean
  /** Called for every traversal step. */
  onEvent?: (event: TraversalEvent) => void
  /** Called when the server asks the client to refetch the static graph. */
  onRefresh?: (reason: string) => void
}

export interface UseLiveTraversalState {
  status: LiveStatus
  /** The MCP session currently being followed, or null while waiting. */
  sessionId: string | null
  /** The last event seq observed — the reconnect hint sent as `?lastSeq=`. */
  lastSeq: number | null
}

// Reconnect backoff: 1s doubling up to 15s, so a flapping backend doesn't get
// hammered but a transient drop recovers quickly.
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 15000
// 1008 = policy violation (auth failure / not a member) — not worth retrying.
const WS_POLICY_VIOLATION = 1008

export function useLiveTraversal(
  orgId: string | null,
  { enabled = true, onEvent, onRefresh }: UseLiveTraversalOptions = {},
): UseLiveTraversalState {
  const [status, setStatus] = useState<LiveStatus>("closed")
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [lastSeq, setLastSeq] = useState<number | null>(null)

  // Keep callbacks in refs so their identity never forces a reconnect.
  const onEventRef = useRef(onEvent)
  const onRefreshRef = useRef(onRefresh)
  onEventRef.current = onEvent
  onRefreshRef.current = onRefresh

  // Latest seq seen, mirrored into a ref so a reconnect reads it synchronously.
  const lastSeqRef = useRef<number | null>(null)

  const send = useCallback((ws: WebSocket, frame: ClientFrame) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame))
  }, [])

  useEffect(() => {
    if (!enabled || !orgId) {
      setStatus("closed")
      return
    }

    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStatus("connecting")

      const hint = lastSeqRef.current
      const query = hint !== null ? `?lastSeq=${hint}` : ""
      const socket = new WebSocket(`${WS_BASE_URL}/orgs/${orgId}/graph/live${query}`)
      ws = socket

      socket.onopen = () => {
        attempt = 0
        send(socket, { type: "hello", lastSeq: lastSeqRef.current })
      }

      socket.onmessage = (ev) => {
        let frame: LiveFrame
        try {
          frame = JSON.parse(ev.data) as LiveFrame
        } catch {
          return
        }
        switch (frame.type) {
          case "following":
          case "switch":
            setSessionId(frame.sessionId)
            setStatus("following")
            break
          case "waiting":
            setSessionId(null)
            setStatus("waiting")
            break
          case "event": {
            const seq = frame.event.seq
            lastSeqRef.current = seq
            setLastSeq(seq)
            onEventRef.current?.(frame.event)
            break
          }
          case "refresh":
            onRefreshRef.current?.(frame.reason)
            break
          case "ping":
            send(socket, { type: "pong" })
            break
        }
      }

      socket.onclose = (ev) => {
        if (disposed || ev.code === WS_POLICY_VIOLATION) {
          setStatus("closed")
          return
        }
        // Unexpected drop — reconnect with backoff; the `?lastSeq=` hint lets
        // the server tell us whether we missed too much.
        setStatus("connecting")
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** attempt,
          RECONNECT_MAX_MS,
        )
        attempt += 1
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      disposed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) {
        ws.onclose = null // suppress the reconnect path on intentional close
        ws.close()
      }
      setStatus("closed")
    }
  }, [orgId, enabled, send])

  return { status, sessionId, lastSeq }
}
