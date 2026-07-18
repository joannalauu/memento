import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, useMyOrgs, useOrgGraph } from "@/lib/api";
import { GraphView } from "@/features/graph/GraphView";
import { Legend } from "@/features/graph/Legend";
import { NodeDetailPanel } from "@/features/graph/NodeDetailPanel";
import type { GraphNode } from "@/features/graph/types";

function App() {
  const [orgId, setOrgId] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [themeKey, setThemeKey] = useState(0);

  const {
    data: orgs,
    isPending: orgsLoading,
    error: orgsError,
  } = useMyOrgs();
  const { data: graph, error: graphError } = useOrgGraph(orgId ?? undefined);

  // Show the spinner while orgs load, or while a selected org's graph is still
  // in flight (no data and no error yet).
  const loading =
    orgsLoading || (!!orgId && !graph && !graphError);

  // Auto-select the first org once the list loads.
  useEffect(() => {
    if (orgs && orgId === null) setOrgId(orgs[0]?.id ?? null);
  }, [orgs, orgId]);

  // Clear the selected node whenever the active org changes.
  useEffect(() => {
    setSelected(null);
  }, [orgId]);

  const error =
    orgsError instanceof ApiError && orgsError.isUnauthorized
      ? "Not authenticated — sign in to view the graph."
      : orgs && orgs.length === 0
        ? "You are not a member of any org."
        : (orgsError?.message ?? graphError?.message ?? null);

  const toggleTheme = useCallback(() => {
    document.documentElement.classList.toggle("dark");
    setThemeKey((k) => k + 1);
  }, []);

  // Hop to another node by id (from the detail panel), keeping the object
  // reference the force graph knows so the selection ring lands on it.
  const hop = useCallback(
    (nodeId: string) => {
      const target = graph?.nodes.find((n) => n.id === nodeId);
      if (target) setSelected(target);
    },
    [graph],
  );

  return (
    <div className="bg-background text-foreground flex h-screen flex-col">
      <header className="flex items-center justify-between gap-4 border-b px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold">Knowledge Graph</h1>
          {orgs && orgs.length > 1 && orgId && (
            <Select value={orgId} onValueChange={setOrgId}>
              <SelectTrigger size="sm" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {orgs.map((o) => (
                  <SelectItem key={o.id} value={o.id}>
                    {o.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {orgs && orgs.length === 1 && (
            <span className="text-muted-foreground text-sm">{orgs[0].name}</span>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleTheme}
          aria-label="Toggle theme"
        >
          <Sun className="dark:hidden" />
          <Moon className="hidden dark:block" />
        </Button>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          {loading && (
            <div className="text-muted-foreground grid h-full place-items-center text-sm">
              Loading…
            </div>
          )}
          {error && !graph && (
            <div className="grid h-full place-items-center p-8">
              <p className="text-destructive max-w-md text-center text-sm">
                {error}
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

        {selected && orgId && (
          <NodeDetailPanel
            orgId={orgId}
            node={selected}
            onClose={() => setSelected(null)}
            onHop={hop}
          />
        )}
      </div>
    </div>
  );
}

export default App;
