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

import { queryKeys, useOrgGraph, useOrgRepos } from "@/lib/api"
import type { GraphFilters, GraphPayload } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { useTheme } from "@/components/app-shell/theme-context"
import { AskBar } from "./AskBar"
import { GraphView } from "./GraphView"
import { Legend } from "./Legend"
import { NodeDetailPanel } from "./NodeDetailPanel"
import { RepoFilter } from "./RepoFilter"
import type { GraphLink, GraphNode } from "./types"

// Rendered while the user has deselected every repo — no fetch, just an empty
// canvas so the live/highlight machinery still has a payload to sit on.
const EMPTY_GRAPH: GraphPayload = { nodes: [], links: [] }
import { useAskGraph } from "./useAskGraph"
import { useLiveHighlight } from "./useLiveHighlight"
import { useLiveTraversal } from "./useLiveTraversal"
import { usePacingBuffer } from "./pacingBuffer"

export function GraphPage() {
  const { orgId } = useActiveOrg()
  const { version: themeKey } = useTheme()
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const qc = useQueryClient()

  // Repo scoping (default: every repo). Track the repos turned OFF, so the
  // "all selected" default is the empty set — no init against the async list.
  const { data: repos = [] } = useOrgRepos(orgId)
  const [deselected, setDeselected] = useState<Set<string>>(new Set())
  const repoNames = useMemo(
    () => repos.map((r) => `${r.owner}/${r.name}`),
    [repos],
  )
  const selectedRepos = useMemo(
    () => repoNames.filter((name) => !deselected.has(name)),
    [repoNames, deselected],
  )
  const allSelected = deselected.size === 0
  // Distinct from "no repos exist": the user turned every repo off on purpose.
  const noneSelected = repoNames.length > 0 && selectedRepos.length === 0

  // Only send a filter for a strict subset; "all" hits the whole-org cache.
  const filters = useMemo<GraphFilters | undefined>(
    () => (allSelected ? undefined : { repos: selectedRepos }),
    [allSelected, selectedRepos],
  )

  const { data: fetchedGraph, error: graphError } = useOrgGraph(orgId, filters, {
    enabled: !!orgId && !noneSelected,
  })
  const graph = noneSelected ? EMPTY_GRAPH : fetchedGraph

  const toggleRepo = useCallback((name: string) => {
    setDeselected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])
  const selectAllRepos = useCallback(() => setDeselected(new Set()), [])
  const clearRepos = useCallback(
    () => setDeselected(new Set(repoNames)),
    [repoNames],
  )

  const loading = !noneSelected && !graph && !graphError

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
    enabled: !!fetchedGraph && !noneSelected,
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

  // Clear selection, repo scoping, + any in-flight live state whenever the
  // active org changes (repo names are org-specific).
  useEffect(() => {
    setSelected(null)
    setDeselected(new Set())
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
        {repoNames.length > 0 && (
          <RepoFilter
            repos={repoNames}
            deselected={deselected}
            onToggle={toggleRepo}
            onSelectAll={selectAllRepos}
            onClear={clearRepos}
          />
        )}
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
        {noneSelected && (
          <div className="text-muted-foreground grid h-full place-items-center text-sm">
            No repositories selected.
          </div>
        )}
        {!noneSelected && graph && (
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
            {liveStatus === "following" && (
              <div className="text-muted-foreground bg-background/70 pointer-events-none absolute top-3 right-3 rounded-full border px-2.5 py-1 text-xs backdrop-blur">
                ● live
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
