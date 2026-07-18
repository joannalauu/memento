/**
 * Auth helpers for the Auth0 plate.
 *
 * The backend drives an httpOnly-cookie OAuth flow: `/auth/login` redirects to
 * Auth0, `/auth/callback` sets the session cookies, and `/auth/logout` clears
 * them. There is no JS-readable token — the browser just needs to be sent to
 * these full-page endpoints, so these are location redirects, not fetches.
 */
import { API_BASE_URL } from "./config"

/** Send the browser through the Auth0 login flow. */
export function login(): void {
  window.location.href = `${API_BASE_URL}/auth/login`
}

/** Send the browser through the Auth0 logout flow (clears session cookies). */
export function logout(): void {
  window.location.href = `${API_BASE_URL}/auth/logout`
}
