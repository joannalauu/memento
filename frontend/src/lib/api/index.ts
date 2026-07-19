/**
 * Public entry point for the API fetching layer.
 *
 * Import hooks and fetchers from here:
 *
 *   import { useMyOrgs, useCreateApiKey, ApiError } from "@/lib/api"
 *
 * Backed by React Query + a thin fetch client. Auth rides on Auth0 httpOnly
 * cookies (see auth.ts); every request sends credentials automatically.
 */
export * from "./types"
export { API_BASE_URL } from "./config"
export { ApiError, setUnauthorizedHandler } from "./http"
export { streamSSE } from "./stream"
export { queryKeys } from "./query-keys"
export { createQueryClient } from "./query-client"
export { ApiProvider } from "./provider"
export { login, logout } from "./auth"

// Resource fetchers + hooks
export * from "./resources/users"
export * from "./resources/orgs"
export * from "./resources/api-keys"
export * from "./resources/graph"
export * from "./resources/documents"
