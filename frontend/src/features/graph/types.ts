// Graph view types. The canonical wire shapes live in the shared API layer
// (src/lib/api/types.ts); this module re-exports them and adds the render-time
// augmentation react-force-graph needs — nodes gain x/y during simulation, and
// links have their string endpoints swapped for node object references.

import type {
  GraphLink as ApiGraphLink,
  GraphNode as ApiGraphNode,
} from "@/lib/api";

export type {
  Confidence,
  EdgeKind,
  GraphNodeMeta,
  NodeDetail,
  NodeType,
  Org,
  RelatedDecision,
  StalenessStatus,
} from "@/lib/api";

// react-force-graph mutates x/y in place during the force simulation.
export type GraphNode = ApiGraphNode & {
  x?: number;
  y?: number;
};

// The library swaps string ids for node object refs after load.
export type GraphLink = Omit<ApiGraphLink, "source" | "target"> & {
  source: string | GraphNode;
  target: string | GraphNode;
};

export interface GraphPayload {
  nodes: GraphNode[];
  links: GraphLink[];
}
