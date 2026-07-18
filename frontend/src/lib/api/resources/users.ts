/**
 * Current user — fastapi-users' `/users/me` (GET / PATCH / DELETE).
 *
 * This is the session anchor: a successful `GET /users/me` means the Auth0
 * cookie is valid, a 401 means the browser isn't logged in. `useMe` is the
 * natural gate for "am I authenticated?" checks in the app shell.
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
import type { User, UserUpdate } from "../types"

export const usersApi = {
  /** `GET /users/me` — the authenticated user's own account. */
  me: (signal?: AbortSignal) => request<User>("/users/me", { signal }),

  /** `PATCH /users/me`. */
  updateMe: (body: UserUpdate) =>
    request<User>("/users/me", { method: "PATCH", body }),

  /** `DELETE /users/me` — deletes the current user's account. */
  deleteMe: () => request<void>("/users/me", { method: "DELETE" }),
}

/**
 * The authenticated user. A 401 (not logged in) is not retried — the global
 * unauthorized handler decides whether to redirect through login. Pass
 * `retry: false` / `throwOnError` overrides via `options` if a page wants to
 * render its own signed-out state instead.
 */
export function useMe(options?: Partial<UseQueryOptions<User>>) {
  return useQuery({
    queryKey: queryKeys.users.me(),
    queryFn: ({ signal }) => usersApi.me(signal),
    ...options,
  })
}

export function useUpdateMe(
  options?: UseMutationOptions<User, Error, UserUpdate>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UserUpdate) => usersApi.updateMe(body),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.setQueryData(queryKeys.users.me(), data)
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useDeleteMe(
  options?: UseMutationOptions<void, Error, void>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => usersApi.deleteMe(),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      // The account is gone — drop all cached data for the dead session.
      qc.clear()
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}
