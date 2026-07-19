/**
 * Client-side pacing buffer (T4.7).
 *
 * The backend emits traversal hops far faster than a human can track — a walk
 * can fire dozens of events in a few milliseconds. This buffer sits between the
 * transport and the renderer: it fills at arrival speed and releases **one hop
 * at a time** on a steady, legible cadence, in `seq` order, draining to empty
 * even after the answer has already landed in the terminal.
 *
 * Transport-agnostic by design — it consumes `TraversalEvent`s, not a socket, so
 * the same buffer serves the T4.6a WebSocket and a future T4.5 SSE stream. It is
 * purely client-side: emission is never throttled (the events are real and
 * already computed), and nothing is persisted — the queue is discarded on close.
 */
import { useEffect, useMemo, useRef } from "react"

import type { TraversalEvent } from "./live-types"

// Cadence at shallow depth — the "one hop every ~300ms" a viewer can follow.
const BASE_DELAY_MS = 300
// Floor the adaptive delay approaches under a deep backlog, so a 500-hop
// traversal compresses instead of running for minutes.
const MIN_DELAY_MS = 80
// Backlog depth at which the delay is roughly halved — controls how quickly a
// growing queue accelerates toward MIN_DELAY_MS.
const DEPTH_SOFTCAP = 12

export interface PacingBufferOptions {
  /** Called once per released event, on the paced cadence. */
  onRelease: (event: TraversalEvent) => void
  /** Override the shallow-depth per-hop delay (e.g. slower for demo drama). */
  baseDelayMs?: number
}

export interface PacingBuffer {
  /** Queue an event; (re)starts the release loop if idle. */
  enqueue: (event: TraversalEvent) => void
  /** Drop everything pending and stop the loop (e.g. session switch). */
  clear: () => void
  /** Stop the loop and release the reference (unmount). */
  dispose: () => void
  /** Current backlog depth — handy for a debug readout. */
  readonly depth: number
}

/**
 * Create a pacing buffer. Framework-agnostic — call from a hook (see
 * {@link usePacingBuffer}) or directly from an SSE consumer.
 */
export function createPacingBuffer({
  onRelease,
  baseDelayMs = BASE_DELAY_MS,
}: PacingBufferOptions): PacingBuffer {
  // Pending events, released lowest-`seq` first. Kept small (drains steadily),
  // so a linear min-scan at release time is cheaper than keeping it sorted.
  const pending: TraversalEvent[] = []
  let timer: ReturnType<typeof setTimeout> | null = null

  // Adaptive cadence: steady ~baseDelayMs when shallow, compressing toward
  // MIN_DELAY_MS as the backlog grows so huge traversals don't drag on.
  const nextDelay = (): number => {
    const scaled = baseDelayMs / (1 + pending.length / DEPTH_SOFTCAP)
    return Math.max(MIN_DELAY_MS, Math.min(baseDelayMs, scaled))
  }

  const releaseOne = (): void => {
    timer = null
    if (pending.length === 0) return // drained — loop stops until next enqueue

    // Pull the lowest-seq event: defensive against out-of-order arrival so the
    // path still animates in traversal order.
    let minIdx = 0
    for (let i = 1; i < pending.length; i++) {
      if (pending[i].seq < pending[minIdx].seq) minIdx = i
    }
    const [event] = pending.splice(minIdx, 1)
    onRelease(event)

    if (pending.length > 0) timer = setTimeout(releaseOne, nextDelay())
  }

  const enqueue = (event: TraversalEvent): void => {
    pending.push(event)
    if (timer === null) timer = setTimeout(releaseOne, nextDelay())
  }

  const stop = (): void => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const clear = (): void => {
    pending.length = 0
    stop()
  }

  return {
    enqueue,
    clear,
    dispose: clear,
    get depth() {
      return pending.length
    },
  }
}

export interface UsePacingBufferResult {
  enqueue: (event: TraversalEvent) => void
  clear: () => void
}

/**
 * React wrapper around {@link createPacingBuffer}. The buffer instance is stable
 * for the component's lifetime; `onRelease` is read through a ref so changing its
 * identity never rebuilds the buffer (and never drops the in-flight queue).
 */
export function usePacingBuffer({
  onRelease,
  baseDelayMs,
}: PacingBufferOptions): UsePacingBufferResult {
  const onReleaseRef = useRef(onRelease)
  onReleaseRef.current = onRelease

  const buffer = useMemo(
    () =>
      createPacingBuffer({
        onRelease: (event) => onReleaseRef.current(event),
        baseDelayMs,
      }),
    [baseDelayMs],
  )

  useEffect(() => () => buffer.dispose(), [buffer])

  return { enqueue: buffer.enqueue, clear: buffer.clear }
}
