/**
 * Shared QueryClient factory with app-wide defaults.
 *
 * Auth failures (401) and other 4xx client errors are not retried — only
 * transient failures are worth a second attempt. 401s additionally fire the
 * global unauthorized handler from http.ts, so retrying them is pointless.
 */
import { QueryClient } from "@tanstack/react-query"

import { ApiError } from "./http"

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status < 500) return false
          return failureCount < 2
        },
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}
