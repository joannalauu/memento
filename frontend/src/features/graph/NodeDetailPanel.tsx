import { ExternalLink, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useGraphNode } from "@/lib/api";
import { NODE_TYPE_LABELS } from "./palette";
import type { GraphNode, RelatedDecision, StalenessStatus } from "./types";

interface NodeDetailPanelProps {
  orgId: string;
  node: GraphNode;
  onClose: () => void;
  onHop: (nodeId: string) => void;
}

const STALENESS_VARIANT: Record<
  StalenessStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  fresh: "secondary",
  stale: "destructive",
  gap: "outline",
};

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function DecisionRow({
  d,
  onHop,
}: {
  d: RelatedDecision;
  onHop: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onHop(d.id)}
      className="hover:bg-accent block w-full rounded-md px-2 py-2 text-left transition-colors"
    >
      <div className="truncate text-sm font-medium">{d.label}</div>
      <div className="text-muted-foreground mt-0.5 flex items-center gap-2 text-xs">
        {d.prNumber != null && <span>PR #{d.prNumber}</span>}
        {d.author && <span>{d.author}</span>}
        <span>{fmtDate(d.date)}</span>
        {d.stalenessStatus && d.stalenessStatus !== "fresh" && (
          <Badge variant={STALENESS_VARIANT[d.stalenessStatus]}>
            {d.stalenessStatus}
          </Badge>
        )}
      </div>
    </button>
  );
}

export function NodeDetailPanel({
  orgId,
  node,
  onClose,
  onHop,
}: NodeDetailPanelProps) {
  const { data: detail, isPending: loading, error } = useGraphNode(
    orgId,
    node.id,
  );

  const isDecision = node.type === "decision";

  return (
    <aside className="bg-card flex h-full w-[360px] shrink-0 flex-col border-l">
      <div className="flex items-start justify-between gap-2 p-4">
        <div className="min-w-0">
          <Badge variant="outline" className="mb-2">
            {NODE_TYPE_LABELS[node.type]}
          </Badge>
          <h2 className="text-base leading-snug font-semibold break-words">
            {node.label}
          </h2>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label="Close panel"
        >
          <X />
        </Button>
      </div>
      <Separator />

      <ScrollArea className="flex-1">
        <div className="space-y-4 p-4">
          {loading && (
            <p className="text-muted-foreground text-sm">Loading…</p>
          )}
          {error && (
            <p className="text-destructive text-sm">{error.message}</p>
          )}

          {isDecision && detail && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {detail.confidence && (
                  <Badge variant="secondary">{detail.confidence}</Badge>
                )}
                {detail.stalenessStatus && (
                  <Badge
                    variant={
                      detail.stalenessStatus === "fresh"
                        ? "secondary"
                        : STALENESS_VARIANT[detail.stalenessStatus]
                    }
                  >
                    {detail.stalenessStatus}
                  </Badge>
                )}
              </div>

              <dl className="space-y-2 text-sm">
                {detail.author && (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Author</dt>
                    <dd className="text-right">{detail.author}</dd>
                  </div>
                )}
                {detail.date && (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Date</dt>
                    <dd className="text-right">{fmtDate(detail.date)}</dd>
                  </div>
                )}
                {detail.feature && (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Feature</dt>
                    <dd className="text-right">{detail.feature}</dd>
                  </div>
                )}
                {detail.prNumber != null && (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">PR</dt>
                    <dd className="text-right">
                      {detail.prUrl ? (
                        <a
                          href={detail.prUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary inline-flex items-center gap-1 hover:underline"
                        >
                          #{detail.prNumber}
                          <ExternalLink className="size-3" />
                        </a>
                      ) : (
                        `#${detail.prNumber}`
                      )}
                    </dd>
                  </div>
                )}
              </dl>

              {detail.contentSnapshot && (
                <div>
                  <h3 className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                    Content
                  </h3>
                  <p className="text-sm break-words whitespace-pre-wrap">
                    {detail.contentSnapshot}
                  </p>
                </div>
              )}

              {detail.files?.length ? (
                <div>
                  <h3 className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                    Files
                  </h3>
                  <ul className="space-y-0.5 font-mono text-xs">
                    {detail.files.map((f) => (
                      <li key={f} className="break-all">
                        {f}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {detail.supersededBy && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={() => onHop(detail.supersededBy!)}
                >
                  Superseded by →
                </Button>
              )}
            </>
          )}

          {!isDecision && detail && (
            <div>
              <h3 className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                {detail.relatedDecisions?.length ?? 0} related decision
                {detail.relatedDecisions?.length === 1 ? "" : "s"}
              </h3>
              <div className="-mx-2">
                {detail.relatedDecisions?.map((d) => (
                  <DecisionRow key={d.id} d={d} onHop={onHop} />
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}
