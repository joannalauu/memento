/**
 * State machine for one graph ask (`POST /orgs/{orgId}/graph/ask` over SSE).
 *
 * Owns the request lifecycle (AbortController, per-ask session id) and fans
 * the stream out to two sinks: answer text accumulates in state for the
 * AskBar, and `tool_activity` frames are lifted into full `TraversalEvent`s
 * for the pacing buffer → highlight overlay, so the graph animates the
 * traversal while the answer streams.
 *
 * The `X-Session-Id` header is mandatory for animation: without it the
 * backend's graph tools run untagged and emit no traversal events at all.
 */
import { useCallback, useEffect, useRef, useState } from "react"

import { streamSSE } from "@/lib/api"
import type { AskCitation, AskFrame, AskStatus } from "./ask-types"
import type { TraversalEvent } from "./live-types"

export interface UseAskGraphOptions {
  orgId: string | null
  /** One synthesized event per tool_activity frame — feed the pacing buffer. */
  onTraversalEvent: (event: TraversalEvent) => void
  /** Fired as each ask starts — the page clears the previous trace here. */
  onStart?: () => void
}

export interface UseAskGraphResult {
  /** Start an ask; no-op while one is already streaming. */
  ask: (question: string) => void
  /** Stop streaming but keep whatever answer text already arrived. */
  cancel: () => void
  /** Abort and reset to the collapsed idle state. */
  dismiss: () => void
  status: AskStatus
  answer: string
  citations: AskCitation[]
  error: string | null
}

function isAskFrame(data: unknown): data is AskFrame {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { type?: unknown }).type === "string"
  )
}

export function useAskGraph({
  orgId,
  onTraversalEvent,
  onStart,
}: UseAskGraphOptions): UseAskGraphResult {
  const [status, setStatus] = useState<AskStatus>("idle")
  const [answer, setAnswer] = useState("")
  const [citations, setCitations] = useState<AskCitation[]>([])
  const [error, setError] = useState<string | null>(null)

  const controllerRef = useRef<AbortController | null>(null)
  // Guards that must be readable from stream callbacks without state staleness.
  const statusRef = useRef<AskStatus>("idle")
  statusRef.current = status
  const answerRef = useRef("")
  // Latest callbacks, read through refs so `ask` stays referentially stable.
  const callbacksRef = useRef({ onTraversalEvent, onStart })
  callbacksRef.current = { onTraversalEvent, onStart }

  const ask = useCallback(
    (question: string) => {
      const trimmed = question.trim()
      if (!orgId || !trimmed || statusRef.current === "streaming") return

      controllerRef.current?.abort() // replace a finished (done/error) ask
      const controller = new AbortController()
      controllerRef.current = controller
      const sessionId = crypto.randomUUID()
      // Terminal-frame latch: frames after done/error are ignored, and a
      // stream that closes without one is surfaced as an error.
      let sawTerminal = false

      callbacksRef.current.onStart?.()
      setAnswer("")
      answerRef.current = ""
      setCitations([])
      setError(null)
      setStatus("streaming")

      const handleFrame = (data: unknown) => {
        if (controller.signal.aborted || sawTerminal || !isAskFrame(data)) return
        switch (data.type) {
          case "content_delta":
            answerRef.current += data.content
            setAnswer(answerRef.current)
            break
          case "tool_activity":
            callbacksRef.current.onTraversalEvent({
              sessionId,
              seq: data.seq,
              kind: data.kind,
              nodeId: data.nodeId,
              edgeKind: data.edgeKind,
              fromNodeId: data.fromNodeId,
              source: "web",
              timestamp: new Date().toISOString(),
            })
            break
          case "done":
            sawTerminal = true
            setCitations(data.citations)
            setStatus("done")
            break
          case "error":
            sawTerminal = true
            setError(data.message)
            setStatus("error")
            break
        }
      }

      streamSSE(`/orgs/${orgId}/graph/ask`, {
        body: { question: trimmed },
        headers: { "X-Session-Id": sessionId },
        signal: controller.signal,
        onData: handleFrame,
      })
        .then(() => {
          if (controller.signal.aborted || sawTerminal) return
          // Server closed the stream without done/error: connection died.
          setError("Stream ended unexpectedly")
          setStatus("error")
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted || sawTerminal) return
          setError(err instanceof Error ? err.message : "Request failed")
          setStatus("error")
        })
    },
    [orgId],
  )

  const cancel = useCallback(() => {
    controllerRef.current?.abort()
    if (statusRef.current !== "streaming") return
    // Keep a partial answer on screen; an untouched ask collapses back.
    setStatus(answerRef.current ? "done" : "idle")
  }, [])

  const dismiss = useCallback(() => {
    controllerRef.current?.abort()
    setStatus("idle")
    setAnswer("")
    answerRef.current = ""
    setCitations([])
    setError(null)
  }, [])

  // Org switch or unmount: abort the in-flight request and reset.
  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    dismiss()
  }, [orgId, dismiss])

  return { ask, cancel, dismiss, status, answer, citations, error }
}
