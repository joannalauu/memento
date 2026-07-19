/**
 * Wire contract for the graph Q&A stream (`POST /orgs/{orgId}/graph/ask`).
 *
 * Mirrors the SSE frames in `app/graph/ask.py` the way live-types.ts mirrors
 * the WS envelope. One response interleaves the assistant's answer text with
 * the traversal steps its tool calls emitted — the asker is the watcher.
 */
import type { TraversalEventKind } from "./live-types"

/** A node the answer leaned on; `prNumber` when the node maps to a PR. */
export interface AskCitation {
  nodeId: string
  prNumber: number | null
}

export type AskFrame =
  /** A chunk of the streamed answer text. */
  | { type: "content_delta"; content: string }
  /**
   * One traversal step, carrying what the highlight/pacing pipeline needs
   * (`fromNodeId` for the edge, `seq` for ordering). The consumer lifts this
   * into a full `TraversalEvent` with the ask's own session id.
   */
  | {
      type: "tool_activity"
      nodeId: string
      edgeKind: string | null
      kind: TraversalEventKind
      fromNodeId: string | null
      seq: number
    }
  /** Terminal: the answer finished; citations for the chips row. */
  | { type: "done"; citations: AskCitation[] }
  /** Terminal: the run failed mid-stream. */
  | { type: "error"; code: string; message: string }

/** Lifecycle of one ask, surfaced by useAskGraph. */
export type AskStatus = "idle" | "streaming" | "done" | "error"

/**
 * Derive a GitHub PR URL from a `pr:owner/name:123` node id. The graph payload
 * carries no repo metadata, so this parse is the only derivation available —
 * citations whose node isn't a `pr:` id get no external link (focus-only chip).
 */
export function prUrlFromNodeId(nodeId: string): string | null {
  const match = /^pr:([^:]+\/[^:]+):(\d+)$/.exec(nodeId)
  return match ? `https://github.com/${match[1]}/pull/${match[2]}` : null
}
