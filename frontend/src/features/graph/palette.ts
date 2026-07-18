import type { NodeType } from "./types";

// Categorical node colors, validated for 5-way separation on both a white and
// a near-black canvas. Identity is never carried by color alone — node labels
// and the on-canvas legend always accompany it.
const LIGHT: Record<NodeType, string> = {
  decision: "#2a78d6", // blue — the primary entity
  file: "#0d9488", // teal
  pr: "#eda100", // amber
  engineer: "#c2185b", // rose
  feature: "#007000", // green — feature hubs
};

const DARK: Record<NodeType, string> = {
  decision: "#3987e5",
  file: "#12a392",
  pr: "#c98500",
  engineer: "#e0526e",
  feature: "#008300",
};

export interface Theme {
  node: Record<NodeType, string>;
  link: string; // hairline for most edges
  linkStrong: string; // superseded_by, so the dash reads
  ring: string; // selected-node outline
  labelInk: string; // node label text
}

const LIGHT_THEME: Theme = {
  node: LIGHT,
  link: "#c9c8c1",
  linkStrong: "#8a8a84",
  ring: "#0a0a0a",
  labelInk: "#4a4a46",
};

const DARK_THEME: Theme = {
  node: DARK,
  link: "#3a3a38",
  linkStrong: "#6a6a66",
  ring: "#fafafa",
  labelInk: "#b5b5b0",
};

export function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

export function currentTheme(): Theme {
  return isDark() ? DARK_THEME : LIGHT_THEME;
}

export const NODE_TYPE_ORDER: NodeType[] = [
  "decision",
  "file",
  "pr",
  "engineer",
  "feature",
];

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  decision: "Decision",
  file: "File",
  pr: "PR",
  engineer: "Engineer",
  feature: "Feature",
};
