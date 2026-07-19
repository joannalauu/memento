/**
 * Knowledge graph — `app/graph/routes.py` (`GET /orgs/{org_id}/graph`).
 *
 * Returns the {nodes, links} payload react-force-graph consumes. Optional
 * `repo` / `feature` / `types` filters narrow the scope; the backend caches
 * each scope ~60s, so a short client `staleTime` is a sensible default.
 */
import { useQuery, type UseQueryOptions } from "@tanstack/react-query"

import { request } from "../http"
import { queryKeys } from "../query-keys"
import type {
  GraphFilters,
  GraphPayload,
  NodeDetail,
  ObjectId,
} from "../types"

/**
 * A node id rides the backend's `{node_id:path}` segment raw — it embeds ':'
 * and '/' (e.g. `file:owner/name:src/app.py`). Percent-encode each '/'-split
 * segment so a node's own '#'/'?'/space can't leak into the query or fragment,
 * while the structural '/' separators pass through untouched.
 */
function encodeNodeId(nodeId: string): string {
  return nodeId
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/")
}

export const graphApi = {
  get: (orgId: ObjectId, filters?: GraphFilters, signal?: AbortSignal) =>
    request<GraphPayload>(`/orgs/${orgId}/graph`, {
      signal,
      params: {
        // The endpoint takes a comma-separated `repo`; sort so the scope (and
        // thus the server cache key + client query key) is order-independent.
        repo: filters?.repos?.length
          ? [...filters.repos].sort().join(",")
          : undefined,
        feature: filters?.feature,
        // The endpoint expects a comma-separated `types` string.
        types: filters?.types?.length ? filters.types.join(",") : undefined,
      },
    }),

  /**
   * `GET /orgs/{org_id}/graph/nodes/{node_id}` — detail for one clicked node.
   * Decision nodes return the full snapshot + PR link / author / date; other
   * node types return the decisions they connect to, so a click becomes a hop.
   */
  getNode: (orgId: ObjectId, nodeId: string, signal?: AbortSignal) =>
    request<NodeDetail>(
      `/orgs/${orgId}/graph/nodes/${encodeNodeId(nodeId)}`,
      { signal },
    ),

  /**
   * `POST /orgs/{org_id}/graph/transcribe` — speech-to-text for the ask bar.
   * Uploads recorded audio (multipart) and returns the transcript to drop into
   * the question input. Transcription runs through Backboard/ElevenLabs.
   */
  transcribe: (
    orgId: ObjectId,
    audio: Blob,
    filename: string,
    signal?: AbortSignal,
  ) => {
    const form = new FormData()
    form.append("file", audio, filename)
    return request<{ transcript: string }>(`/orgs/${orgId}/graph/transcribe`, {
      method: "POST",
      body: form,
      signal,
    })
  },
}

export function useOrgGraph(
  orgId: ObjectId | undefined,
  filters?: GraphFilters,
  options?: Partial<UseQueryOptions<GraphPayload>>,
) {
  return useQuery({
    queryKey: queryKeys.graph.detail(orgId ?? "", filters),
    queryFn: ({ signal }) => graphApi.get(orgId as ObjectId, filters, signal),
    enabled: !!orgId,
    staleTime: 60_000,
    ...options,
  })
}

/**
 * Detail for a single node, fetched on click. Disabled until both `orgId` and
 * `nodeId` are present, so it fires only once a node is selected.
 */
export function useGraphNode(
  orgId: ObjectId | undefined,
  nodeId: string | undefined,
  options?: Partial<UseQueryOptions<NodeDetail>>,
) {
  return useQuery({
    queryKey: queryKeys.graph.node(orgId ?? "", nodeId ?? ""),
    queryFn: ({ signal }) =>
      graphApi.getNode(orgId as ObjectId, nodeId as string, signal),
    enabled: !!orgId && !!nodeId,
    ...options,
  })
}
