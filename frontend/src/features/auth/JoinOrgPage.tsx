/**
 * Join-org page ("/join-org?token=…") — the target of an invite email's accept
 * button.
 *
 * Flow:
 *   1. Read the invite token from the query string.
 *   2. Check the session (`useMe`). If signed out, send the browser through
 *      login, returning here afterward via the backend bridge route so the
 *      round trip lands back on this page authenticated.
 *   3. Once authenticated, call the token-only accept endpoint and route the
 *      freshly-joined user into the app. Terminal states (expired, wrong
 *      account, already a member) render an in-app status card.
 */
import { useEffect, useRef, type ReactNode } from "react"
import { Navigate, useSearchParams } from "react-router-dom"
import { Waypoints } from "lucide-react"
import { toast } from "sonner"

import { ApiError, login, useAcceptOrgInviteByToken, useMe } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ThemeToggle } from "@/components/app-shell/ThemeToggle"
import { FullPageSpinner } from "@/components/full-page-state"

/** Human-friendly copy for the accept endpoint's terminal errors. */
function acceptErrorMessage(error: Error): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 403:
        return (
          "This invite was sent to a different email than the one you're " +
          "signed in with. Sign out, sign back in with the invited address, " +
          "and open the link again."
        )
      case 410:
        return "This invite has expired. Ask an admin to send you a new one."
      case 404:
        return "This invite link is invalid or has already been used."
      case 409:
        return "You're already a member of this organization."
    }
  }
  return error.message || "We couldn't accept this invite. Please try again."
}

/** Card chrome shared by the terminal (error / no-token) states. */
function JoinStatusCard({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <div className="bg-background text-foreground relative flex min-h-screen flex-col items-center justify-center p-6">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm">
        <CardHeader className="justify-items-center text-center">
          <div className="bg-primary/10 text-primary mb-2 grid size-12 place-items-center rounded-xl">
            <Waypoints className="size-6" />
          </div>
          <CardTitle className="text-xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        {children ? (
          <CardContent className="flex flex-col gap-3">{children}</CardContent>
        ) : null}
      </Card>
    </div>
  )
}

export function JoinOrgPage() {
  const [params] = useSearchParams()
  const token = params.get("token")

  const me = useMe({ retry: false })
  const signedOut = me.error instanceof ApiError && me.error.isUnauthorized

  const accept = useAcceptOrgInviteByToken({
    onSuccess: (org) => toast.success(`You've joined ${org.name}`),
  })

  // Kick off login once we know the visitor is signed out. The bridge route
  // returns them here authenticated, after which the accept effect below fires.
  useEffect(() => {
    if (token && signedOut) {
      login(`/orgs/invites/${token}/continue`)
    }
  }, [token, signedOut])

  // Accept exactly once, as soon as we have a token and a valid session.
  const accepted = useRef(false)
  const authenticated = !!me.data
  useEffect(() => {
    if (token && authenticated && !accepted.current) {
      accepted.current = true
      accept.mutate(token)
    }
  }, [token, authenticated, accept])

  if (!token) {
    return (
      <JoinStatusCard
        title="Invalid invite link"
        description="This link is missing its invite token. Ask an admin to send you a new invite."
      >
        <Button asChild variant="outline">
          <a href="/">Go to app</a>
        </Button>
      </JoinStatusCard>
    )
  }

  // Signed out: the effect above is redirecting to login — show a spinner
  // rather than flashing an error while the browser navigates away.
  if (me.isPending || signedOut) return <FullPageSpinner />

  if (me.error) {
    return (
      <JoinStatusCard
        title="Something went wrong"
        description={me.error.message ?? "Couldn't verify your session. Please try again."}
      >
        <Button onClick={() => login(`/orgs/invites/${token}/continue`)}>
          Sign in again
        </Button>
      </JoinStatusCard>
    )
  }

  // Authenticated — resolve the accept mutation.
  if (accept.isSuccess) return <Navigate to="/" replace />

  if (accept.isError) {
    return (
      <JoinStatusCard
        title="Couldn't join"
        description={acceptErrorMessage(accept.error)}
      >
        <Button asChild>
          <a href="/">Go to app</a>
        </Button>
      </JoinStatusCard>
    )
  }

  // idle / pending — accepting the invite.
  return <FullPageSpinner />
}
