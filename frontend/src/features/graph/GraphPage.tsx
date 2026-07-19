/**
 * The knowledge-graph view, now a route inside the app shell. The shell owns the
 * header and org selection; this page reads the active org from context and
 * renders the force graph plus the node detail panel.
 *
 * Live traversal pipeline (T4.6 + T4.7): the WS transport (T4.6a) feeds a pacing
 * buffer that releases hops at a legible cadence into the highlight overlay,
 * which lights up nodes/edges over the already-rendered static graph.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { ForceGraphMethods } from "react-force-graph-2d"

import { queryKeys, useOrgGraph } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { useTheme } from "@/components/app-shell/theme-context"
import { AskBar } from "./AskBar"
import { GraphView } from "./GraphView"
import { Legend } from "./Legend"
import { NodeDetailPanel } from "./NodeDetailPanel"
import type { GraphLink, GraphNode } from "./types"
import { useAskGraph } from "./useAskGraph"
import { useLiveHighlight } from "./useLiveHighlight"
import { useLiveTraversal } from "./useLiveTraversal"
import { usePacingBuffer } from "./pacingBuffer"

export function GraphPage() {
  const { orgId } = useActiveOrg()
  const { version: themeKey } = useTheme()
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const qc = useQueryClient()

  const { data: graph, error: graphError } = useOrgGraph(orgId)

  const loading = !graph && !graphError

  // Shared with the live layer so it can drive zoom/particles imperatively.
  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(
    undefined,
  )
  const nodesById = useMemo(
    () => new Map((graph?.nodes ?? []).map((n) => [n.id, n])),
    [graph],
  )

  // Transport → pacing buffer → highlight overlay → GraphView.
  const { highlightRef, apply, reset, renderTick, isTracing } =
    useLiveHighlight({ graphRef, nodesById })
  const { enqueue, clear } = usePacingBuffer({ onRelease: apply })

  // Web Q&A (T4.5): the ask's SSE stream feeds the same buffer/overlay.
  const askGraph = useAskGraph({
    orgId,
    onTraversalEvent: enqueue,
    onStart: () => {
      clear()
      reset()
    },
  })
  // Gate WS events off while an ask streams: the pacing buffer releases the
  // globally-lowest seq with no session awareness, so mixing an ask's fresh
  // seq 0.. with a followed MCP session's high seqs would starve/reorder one
  // of them. The user-initiated ask wins the overlay. Ref-read so the WS
  // hook's onEvent identity stays stable.
  const askStreamingRef = useRef(false)
  askStreamingRef.current = askGraph.status === "streaming"

  const { status: liveStatus } = useLiveTraversal(orgId, {
    enabled: !!graph,
    onEvent: (e) => {
      if (!askStreamingRef.current) enqueue(e)
    },
    // Missed too much to reconcile: drop the in-flight trace and refetch static.
    onRefresh: () => {
      clear()
      reset()
      qc.invalidateQueries({ queryKey: queryKeys.graph.all })
    },
  })

  // Clear selection + any in-flight live state whenever the active org changes.
  useEffect(() => {
    setSelected(null)
    clear()
    reset()
  }, [orgId, clear, reset])

  // Hop to another node by id (from the detail panel), keeping the object
  // reference the force graph knows so the selection ring lands on it.
  const hop = useCallback(
    (nodeId: string) => {
      const target = graph?.nodes.find((n) => n.id === nodeId)
      if (target) setSelected(target)
    },
    [graph],
  )

  return (
    <div className="relative flex h-full min-h-0">
      <div className="relative min-w-0 flex-1">
        {loading && (
          <div className="text-muted-foreground grid h-full place-items-center text-sm">
            Loading…
          </div>
        )}
        {graphError && !graph && (
          <div className="grid h-full place-items-center p-8">
            <p className="text-destructive max-w-md text-center text-sm">
              {graphError.message}
            </p>
          </div>
        )}
        {graph && (
          <>
            <GraphView
              data={graph}
              selectedId={selected?.id ?? null}
              onSelect={setSelected}
              onClear={() => setSelected(null)}
              themeKey={themeKey}
              graphRef={graphRef}
              highlightRef={highlightRef}
              renderTick={renderTick}
              readOnly={isTracing}
            />
            <Legend themeKey={themeKey} />
            <AskBar
              orgId={orgId}
              status={askGraph.status}
              answer={askGraph.answer}
              citations={askGraph.citations}
              error={askGraph.error}
              onAsk={askGraph.ask}
              onCancel={askGraph.cancel}
              onDismiss={askGraph.dismiss}
              onCitationClick={hop}
              getNodeLabel={(id) => nodesById.get(id)?.label ?? null}
            />
            {(liveStatus === "following" || liveStatus === "waiting") && (
              <div className="text-muted-foreground bg-background/70 pointer-events-none absolute top-3 right-3 rounded-full border px-2.5 py-1 text-xs backdrop-blur">
                {liveStatus === "following" ? "● live" : "waiting for session…"}
              </div>
            )}
          </>
        )}
      </div>

      {selected && (
        <NodeDetailPanel
          orgId={orgId}
          node={selected}
          onClose={() => setSelected(null)}
          onHop={hop}
        />
      )}
    </div>
  )
}
