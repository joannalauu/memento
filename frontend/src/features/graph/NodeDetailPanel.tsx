import { ExternalLink, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useGraphNode, useOrgRepos } from "@/lib/api";
import { githubBlobUrl } from "@/lib/github";
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

// Decision content is stored with a "[repo: owner/name] " prefix (injected in
// app/backboard/client.py). Split it off so the repo renders on its own line
// instead of crowding — and truncating — the decision title / related rows.
const REPO_PREFIX = /^\[repo:\s*([^\]]+)\]\s*/;
function splitRepo(text?: string | null): { repo: string | null; body: string } {
  if (!text) return { repo: null, body: "" };
  const m = text.match(REPO_PREFIX);
  return m ? { repo: m[1], body: text.slice(m[0].length) } : { repo: null, body: text };
}

function DecisionRow({
  d,
  onHop,
}: {
  d: RelatedDecision;
  onHop: (id: string) => void;
}) {
  const { repo, body } = splitRepo(d.label);
  return (
    <button
      type="button"
      onClick={() => onHop(d.id)}
      className="hover:bg-accent block w-full rounded-md px-2 py-2 text-left transition-colors"
    >
      <div className="text-xs font-medium break-words">{body}</div>
      <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
        {repo && <span className="break-all">{repo}</span>}
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
  // Prefer the full snapshot (untruncated) once detail loads; fall back to the
  // pre-truncated node label while it's still fetching. Repo is pulled out of
  // the "[repo: …]" prefix so it no longer crowds the title.
  const { repo, body } = splitRepo(detail?.contentSnapshot ?? node.label);
  const title = isDecision ? body.split("\n")[0] || node.label : node.label;

  // Resolve the "owner/name" from the content prefix to a full repo record so
  // the Files list can link to GitHub (owner/name/defaultBranch).
  const { data: repos } = useOrgRepos(orgId);
  const fileRepo = repo
    ? repos?.find((r) => `${r.owner}/${r.name}` === repo)
    : undefined;

  return (
    <aside className="bg-card flex h-full w-[360px] shrink-0 flex-col border-l">
      <div className="flex items-start justify-between gap-2 p-4">
        <div className="min-w-0">
          <Badge variant="outline" className="mb-2">
            {NODE_TYPE_LABELS[node.type]}
          </Badge>
          <h2 className="text-sm leading-snug font-semibold break-words">
            {title}
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
                {repo && (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Repo</dt>
                    <dd className="text-right break-all">{repo}</dd>
                  </div>
                )}
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

              {body && (
                <div>
                  <h3 className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                    Content
                  </h3>
                  <p className="text-sm break-words whitespace-pre-wrap">
                    {body}
                  </p>
                </div>
              )}

              {detail.files?.length ? (
                <div>
                  <h3 className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                    Files
                  </h3>
                  <ul className="space-y-0.5 font-mono text-xs">
                    {detail.files.map((f) => {
                      const url = githubBlobUrl(fileRepo, f);
                      return (
                        <li key={f} className="break-all">
                          {url ? (
                            <a
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary inline-flex items-center gap-1 hover:underline"
                            >
                              {f}
                              <ExternalLink className="size-3 shrink-0" />
                            </a>
                          ) : (
                            f
                          )}
                        </li>
                      );
                    })}
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
