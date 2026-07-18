/**
 * Route gate for authenticated areas. Renders the child routes only when the
 * Auth0 session is valid; an unauthenticated visitor is sent to the landing
 * page at "/".
 */
import { Navigate, Outlet } from "react-router-dom"

import { ApiError, useMe } from "@/lib/api"
import { FullPageError, FullPageSpinner } from "@/components/full-page-state"

export function RequireAuth() {
  const { data: me, isPending, error } = useMe({ retry: false })

  if (isPending) return <FullPageSpinner />
  if (error instanceof ApiError && error.isUnauthorized) {
    return <Navigate to="/" replace />
  }
  if (error || !me) return <FullPageError message={error?.message} />

  return <Outlet />
}
