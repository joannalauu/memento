/**
 * Auth helpers for the Auth0 plate.
 *
 * The backend drives an httpOnly-cookie OAuth flow: `/auth/login` redirects to
 * Auth0, `/auth/callback` sets the session cookies, and `/auth/logout` clears
 * them. There is no JS-readable token — the browser just needs to be sent to
 * these full-page endpoints, so these are location redirects, not fetches.
 */
import { API_BASE_URL } from "./config"

/**
 * Send the browser through the Auth0 login flow.
 *
 * `returnTo` is an optional post-login destination. The backend only honors a
 * same-origin (relative, "/"-prefixed) path — after the Auth0 callback it
 * resolves to the *API* origin, so cross-origin SPA returns must go through a
 * backend redirect bridge (see the org-invite join flow).
 */
export function login(returnTo?: string): void {
  const url = new URL(`${API_BASE_URL}/auth/login`)
  if (returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//")) {
    url.searchParams.set("return_to", returnTo)
  }
  window.location.href = url.toString()
}

/** Send the browser through the Auth0 logout flow (clears session cookies). */
export function logout(): void {
  window.location.href = `${API_BASE_URL}/auth/logout`
}
