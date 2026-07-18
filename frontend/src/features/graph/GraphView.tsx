import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
} from "react-force-graph-2d";
import { currentTheme } from "./palette";
import type { GraphLink, GraphNode, GraphPayload } from "./types";

interface GraphViewProps {
  data: GraphPayload;
  selectedId: string | null;
  onSelect: (node: GraphNode) => void;
  onClear: () => void;
  // bump to re-read theme colors after a light/dark toggle
  themeKey: number;
}

export function GraphView({
  data,
  selectedId,
  onSelect,
  onClear,
  themeKey,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(
    undefined,
  );
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

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      <ForceGraph2D
        ref={fgRef}
        width={size.width}
        height={size.height}
        graphData={data}
        // transparent canvas: the container's bg-background shows through
        backgroundColor="rgba(0,0,0,0)"
        nodeRelSize={1}
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
        linkColor={(l) =>
          l.kind === "superseded_by" ? theme.linkStrong : theme.link
        }
        linkWidth={1}
        linkLineDash={(l) => (l.kind === "superseded_by" ? [4, 3] : null)}
        linkDirectionalArrowLength={3.5}
        linkDirectionalArrowRelPos={1}
        linkLabel={(l) =>
          l.symbols?.length ? `${l.kind} — ${l.symbols.join(", ")}` : l.kind
        }
        onNodeClick={(node) => onSelect(node)}
        onBackgroundClick={onClear}
      />
    </div>
  );
}
