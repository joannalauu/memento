/**
 * API configuration.
 *
 * The backend enables CORS for http://localhost:5173 with credentials, and the
 * Auth0 plate authenticates via httpOnly `id_token`/`access_token` cookies. The
 * frontend dev origin (:5173) and the API (:8000) are the same site, so the
 * SameSite=Lax cookies are sent on cross-origin fetches as long as every
 * request opts in with `credentials: "include"` (see http.ts).
 *
 * Override the base URL per-environment with `VITE_API_BASE_URL`.
 */
export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "")

/**
 * WebSocket origin for the live traversal channel (`WS /orgs/{orgId}/graph/live`),
 * derived from {@link API_BASE_URL} by swapping the scheme (`http`->`ws`,
 * `https`->`wss`). The same session cookie rides the WS handshake, so — like the
 * fetch layer — no token is handled in JS.
 */
export const WS_BASE_URL: string = API_BASE_URL.replace(/^http/, "ws")
