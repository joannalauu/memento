/**
 * API keys — `app/api_auth/routes.py` (mounted at `/api-keys`).
 *
 * The raw secret is returned exactly once from `create` and never again, so the
 * create mutation hands the full {@link ApiKeyCreated} back to the caller to
 * display before it's lost.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
} from "@tanstack/react-query"

import { request } from "../http"
import { queryKeys } from "../query-keys"
import type { ApiKey, ApiKeyCreate, ApiKeyCreated, ObjectId } from "../types"

export const apiKeysApi = {
  /** `GET /api-keys` — the user's keys (secrets withheld). */
  list: (signal?: AbortSignal) =>
    request<ApiKey[]>("/api-keys", { signal }),

  /** `GET /api-keys/{key_id}`. */
  get: (keyId: ObjectId, signal?: AbortSignal) =>
    request<ApiKey>(`/api-keys/${keyId}`, { signal }),

  /** `POST /api-keys` — returns the raw secret once. */
  create: (body: ApiKeyCreate) =>
    request<ApiKeyCreated>("/api-keys", { method: "POST", body }),

  /**
   * `POST /api-keys/{key_id}/regenerate` — overwrites the key's secret in place.
   * The old key stops authenticating immediately; the new raw secret is returned
   * exactly once.
   */
  regenerate: (keyId: ObjectId) =>
    request<ApiKeyCreated>(`/api-keys/${keyId}/regenerate`, { method: "POST" }),

  /** `DELETE /api-keys/{key_id}` — revokes immediately. */
  remove: (keyId: ObjectId) =>
    request<void>(`/api-keys/${keyId}`, { method: "DELETE" }),
}

export function useApiKeys(options?: Partial<UseQueryOptions<ApiKey[]>>) {
  return useQuery({
    queryKey: queryKeys.apiKeys.lists(),
    queryFn: ({ signal }) => apiKeysApi.list(signal),
    ...options,
  })
}

export function useApiKey(
  keyId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<ApiKey>>,
) {
  return useQuery({
    queryKey: queryKeys.apiKeys.detail(keyId ?? ""),
    queryFn: ({ signal }) => apiKeysApi.get(keyId as ObjectId, signal),
    enabled: !!keyId,
    ...options,
  })
}

export function useCreateApiKey(
  options?: UseMutationOptions<ApiKeyCreated, Error, ApiKeyCreate>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ApiKeyCreate) => apiKeysApi.create(body),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.lists() })
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useRegenerateApiKey(
  options?: UseMutationOptions<ApiKeyCreated, Error, ObjectId>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (keyId: ObjectId) => apiKeysApi.regenerate(keyId),
    ...options,
    onSuccess: (data, keyId, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.lists() })
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.detail(keyId) })
      options?.onSuccess?.(data, keyId, ...rest)
    },
  })
}

export function useDeleteApiKey(
  options?: UseMutationOptions<void, Error, ObjectId>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (keyId: ObjectId) => apiKeysApi.remove(keyId),
    ...options,
    onSuccess: (data, keyId, ...rest) => {
      qc.removeQueries({ queryKey: queryKeys.apiKeys.detail(keyId) })
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.lists() })
      options?.onSuccess?.(data, keyId, ...rest)
    },
  })
}
