/**
 * The knowledge-graph view, now a route inside the app shell. The shell owns the
 * header and org selection; this page reads the active org from context and
 * renders the force graph plus the node detail panel.
 */
import { useCallback, useEffect, useState } from "react"

import { useOrgGraph } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { useTheme } from "@/components/app-shell/theme-context"
import { GraphView } from "./GraphView"
import { Legend } from "./Legend"
import { NodeDetailPanel } from "./NodeDetailPanel"
import type { GraphNode } from "./types"

export function GraphPage() {
  const { orgId } = useActiveOrg()
  const { version: themeKey } = useTheme()
  const [selected, setSelected] = useState<GraphNode | null>(null)

  const { data: graph, error: graphError } = useOrgGraph(orgId)

  const loading = !graph && !graphError

  // Clear the selected node whenever the active org changes.
  useEffect(() => {
    setSelected(null)
  }, [orgId])

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
            />
            <Legend themeKey={themeKey} />
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
