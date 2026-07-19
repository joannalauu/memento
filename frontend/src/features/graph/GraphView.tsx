import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
} from "react-force-graph-2d";
import { currentTheme } from "./palette";
import type { GraphLink, GraphNode, GraphPayload } from "./types";
import {
  EDGE_FADE_MS,
  NODE_FADE_MS,
  edgeKey,
  type HighlightState,
} from "./useLiveHighlight";

interface GraphViewProps {
  data: GraphPayload;
  selectedId: string | null;
  onSelect: (node: GraphNode) => void;
  onClear: () => void;
  // bump to re-read theme colors after a light/dark toggle
  themeKey: number;
  // Live-traversal overlay (T4.6). Optional so non-live callers are unaffected.
  graphRef?: React.RefObject<
    ForceGraphMethods<GraphNode, GraphLink> | undefined
  >;
  highlightRef?: React.RefObject<HighlightState>;
  // Changes each animation frame to force a repaint of the fading overlay.
  renderTick?: number;
  // While tracing, the graph is read-only (no drag/select).
  readOnly?: boolean;
}

// A link endpoint is a node id before layout and a node object after it.
function endpointId(end: string | GraphNode): string {
  return typeof end === "string" ? end : end.id;
}

// #rrggbb -> rgba() so a highlighted edge can fade via its alpha channel.
function hexAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function GraphView({
  data,
  selectedId,
  onSelect,
  onClear,
  themeKey,
  graphRef,
  highlightRef,
  renderTick,
  readOnly = false,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const internalFgRef = useRef<
    ForceGraphMethods<GraphNode, GraphLink> | undefined
  >(undefined);
  // Prefer the ref the parent shares (so the live layer can drive zoom/particles).
  const fgRef = graphRef ?? internalFgRef;
  const [size, setSize] = useState({ width: 0, height: 0 });

  // Re-read palette on theme toggle. themeKey changes on toggle, forcing this
  // memo to recompute so the canvas callbacks close over the new colors.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  const theme = useMemo(() => currentTheme(), [themeKey]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setSize({ width, height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // A little extra charge so shared-file hubs read as clusters, not a hairball.
  useEffect(() => {
    fgRef.current?.d3Force("charge")?.strength(-120);
  }, [data]);

  // Fade [0..1] for a live-traversed edge, 0 if it isn't highlighted. Undirected
  // match so a `supersedes` hop lights the same rendered `superseded_by` edge.
  const hotEdgeAlpha = (l: GraphLink): number => {
    const state = highlightRef?.current;
    if (!state) return 0;
    const h = state.edges.get(
      edgeKey(endpointId(l.source), endpointId(l.target)),
    );
    if (!h) return 0;
    return Math.max(0, 1 - (performance.now() - h.at) / EDGE_FADE_MS);
  };

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      // renderTick changes each animation frame while tracing; reading it here
      // ties this component's render to the highlight loop so the canvas repaints.
      data-render-tick={renderTick}
    >
      <ForceGraph2D
        ref={fgRef}
        width={size.width}
        height={size.height}
        graphData={data}
        // transparent canvas: the container's bg-background shows through
        backgroundColor="rgba(0,0,0,0)"
        nodeRelSize={1}
        enableNodeDrag={!readOnly}
        nodeLabel={(n) => `${n.type}: ${n.label}`}
        nodeCanvasObject={(node, ctx, globalScale) => {
          const r = 3 + 1.5 * Math.sqrt(node.val ?? 1);
          ctx.beginPath();
          ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI);
          ctx.fillStyle = theme.node[node.type];
          ctx.fill();
          if (node.id === selectedId) {
            ctx.strokeStyle = theme.ring;
            ctx.lineWidth = 2 / globalScale;
            ctx.stroke();
          }
          // Live-traversal highlight: entry (semantic landing) gets a larger,
          // brighter halo; hop (structural) a tighter ring. Both fade with age.
          const hl = highlightRef?.current?.nodes.get(node.id);
          if (hl) {
            const alpha = Math.max(
              0,
              1 - (performance.now() - hl.at) / NODE_FADE_MS,
            );
            if (alpha > 0) {
              const isEntry = hl.kind === "entry";
              const color = isEntry
                ? theme.highlightEntry
                : theme.highlightHop;
              const haloR = r + (isEntry ? 6 : 3);
              ctx.save();
              // soft filled glow
              ctx.globalAlpha = alpha * (isEntry ? 0.28 : 0.2);
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, haloR, 0, 2 * Math.PI);
              ctx.fill();
              // crisp ring
              ctx.globalAlpha = alpha;
              ctx.strokeStyle = color;
              ctx.lineWidth = (isEntry ? 2.5 : 1.5) / globalScale;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, haloR, 0, 2 * Math.PI);
              ctx.stroke();
              ctx.restore();
            }
          }
          // Direct labels appear once zoomed in — legible without clutter.
          if (globalScale > 1.2) {
            ctx.font = `${11 / globalScale}px 'Geist Variable', sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillStyle = theme.labelInk;
            ctx.fillText(
              node.label,
              node.x ?? 0,
              (node.y ?? 0) + r + 2 / globalScale,
            );
          }
        }}
        nodePointerAreaPaint={(node, color, ctx) => {
          const r = 3 + 1.5 * Math.sqrt(node.val ?? 1);
          ctx.beginPath();
          ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();
        }}
        linkColor={(l) => {
          const hot = hotEdgeAlpha(l);
          if (hot > 0) return hexAlpha(theme.highlightEdge, 0.35 + 0.65 * hot);
          return l.kind === "superseded_by" ? theme.linkStrong : theme.link;
        }}
        linkWidth={(l) => {
          const hot = hotEdgeAlpha(l);
          return hot > 0 ? 1 + 2.5 * hot : 1;
        }}
        linkLineDash={(l) => (l.kind === "superseded_by" ? [4, 3] : null)}
        linkDirectionalArrowLength={3.5}
        linkDirectionalArrowRelPos={1}
        // A particle rides the freshly-traversed edge — the hop's direction of travel.
        linkDirectionalParticles={(l) => (hotEdgeAlpha(l) > 0 ? 2 : 0)}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleColor={() => theme.highlightEdge}
        linkLabel={(l) =>
          l.symbols?.length ? `${l.kind} — ${l.symbols.join(", ")}` : l.kind
        }
        onNodeClick={(node) => {
          if (!readOnly) onSelect(node);
        }}
        onBackgroundClick={() => {
          if (!readOnly) onClear();
        }}
      />
    </div>
  );
}
