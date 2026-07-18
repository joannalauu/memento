/**
 * Root dispatcher ("/"). Sends the browser where it belongs based on session
 * state:
 *   - signed out          → landing page
 *   - signed in, no org    → onboarding (create an org as admin)
 *   - signed in, admin     → admin home
 *   - signed in, member    → user home
 *
 * Invitees are accepted server-side and land here already a member, so this is
 * also where a freshly-joined user gets routed to their home.
 */
import { Navigate } from "react-router-dom"

import { ApiError, useMe, useMyOrgs } from "@/lib/api"
import { FullPageError, FullPageSpinner } from "@/components/full-page-state"
import { LandingPage } from "./LandingPage"

export function RootPage() {
  const me = useMe({ retry: false })
  const signedIn = !!me.data
  const orgs = useMyOrgs({ retry: false, enabled: signedIn })

  if (me.isPending) return <FullPageSpinner />
  if (me.error instanceof ApiError && me.error.isUnauthorized) {
    return <LandingPage />
  }
  if (me.error || !me.data) return <FullPageError message={me.error?.message} />

  // Signed in — resolve org membership before routing.
  if (orgs.isPending) return <FullPageSpinner />
  if (orgs.error) return <FullPageError message={orgs.error.message} />
  if (!orgs.data.length) return <Navigate to="/onboarding" replace />

  const active = orgs.data[0]
  const role = active.members.find((m) => m.userId === me.data.id)?.role
  return <Navigate to={role === "admin" ? "/admin" : "/home"} replace />
}
