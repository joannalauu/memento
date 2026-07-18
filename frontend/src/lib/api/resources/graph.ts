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
import type { GraphFilters, GraphPayload, ObjectId } from "../types"

export const graphApi = {
  get: (orgId: ObjectId, filters?: GraphFilters, signal?: AbortSignal) =>
    request<GraphPayload>(`/orgs/${orgId}/graph`, {
      signal,
      params: {
        repo: filters?.repo,
        feature: filters?.feature,
        // The endpoint expects a comma-separated `types` string.
        types: filters?.types?.length ? filters.types.join(",") : undefined,
      },
    }),
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
