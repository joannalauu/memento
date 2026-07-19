/**
 * GitHub App connection for the active org (admin-only surface — rendered from
 * the admin dashboard, which non-admins can't reach). When the org isn't yet
 * bound to an installation it offers a Connect button that redirects the browser
 * to GitHub's install page; once connected it lists the synced repositories and
 * offers a "Manage repositories" button to change the selection.
 *
 * The connect flow opens in a new tab: the returned install URL points at
 * github.com, and GitHub redirects back through the backend `/github/setup`
 * endpoint (which binds the installation and syncs repos) to
 * `GITHUB_POST_INSTALL_REDIRECT_URL`. The frontend is not involved in the
 * callback itself.
 *
 * Repository selection is owned by GitHub, not by us: which repos an org can see
 * is the App installation's repository access list. So "add / remove repos"
 * isn't a local mutation — it's the same connect flow. For an already-installed
 * App, GitHub redirects `/apps/{slug}/installations/new` straight to that
 * installation's configure page, where the admin edits the selection. On return,
 * `/github/setup` re-syncs and the `installation_repositories` webhook reconciles
 * adds and removals. Hence the manage button reuses `useConnectGithub`.
 */
import { GitBranch, Loader2, Settings } from "lucide-react"
import { toast } from "sonner"

import { useConnectGithub, useOrgRepos } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

export function GithubSection() {
  const { org, orgId } = useActiveOrg()
  const connected = org.githubInstallationId != null

  return (
    <Card>
      <CardHeader>
        <CardTitle>GitHub</CardTitle>
        <CardDescription>
          Connect a GitHub App installation to sync this organization's
          repositories.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {connected ? <ConnectedState orgId={orgId} /> : <ConnectButton orgId={orgId} />}
      </CardContent>
    </Card>
  )
}

function ConnectButton({ orgId }: { orgId: string }) {
  const connect = useConnectGithub({
    onError: (err) =>
      toast.error(err.message || "Couldn't start GitHub connection."),
  })

  const onClick = () => {
    connect.mutate(orgId, {
      // Open github.com in a new tab. GitHub redirects back through the backend
      // `/github/setup` endpoint, so the install completes in that tab; this tab
      // stays put.
      onSuccess: ({ installUrl }) => {
        window.open(installUrl, "_blank", "noopener,noreferrer")
      },
    })
  }

  // The tab isn't navigating away, so only reflect the in-flight request.
  const pending = connect.isPending

  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed p-6 text-center">
      <div className="bg-muted text-muted-foreground grid size-10 place-items-center rounded-full">
        <GitBranch className="size-5" />
      </div>
      <div>
        <p className="text-sm font-medium">Not connected</p>
        <p className="text-muted-foreground text-sm">
          Install the GitHub App to give this org access to your repositories.
        </p>
      </div>
      <Button onClick={onClick} disabled={pending}>
        {pending ? <Loader2 className="animate-spin" /> : <GitBranch />}
        Connect GitHub
      </Button>
    </div>
  )
}

function ManageReposButton({ orgId }: { orgId: string }) {
  const connect = useConnectGithub({
    onError: (err) =>
      toast.error(err.message || "Couldn't open GitHub repository settings."),
  })

  const onClick = () => {
    connect.mutate(orgId, {
      // Same new-tab navigation as the initial connect: GitHub sends an
      // already-installed App to its configure page in a new tab, where the
      // admin edits the repository selection.
      onSuccess: ({ installUrl }) => {
        window.open(installUrl, "_blank", "noopener,noreferrer")
      },
    })
  }

  const pending = connect.isPending

  return (
    <Button
      variant="outline"
      size="sm"
      className="ml-auto"
      onClick={onClick}
      disabled={pending}
    >
      {pending ? <Loader2 className="animate-spin" /> : <Settings />}
      Manage repositories
    </Button>
  )
}

function ConnectedState({ orgId }: { orgId: string }) {
  const { data: repos, isPending, error } = useOrgRepos(orgId)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <GitBranch className="size-4" />
        <span className="text-sm font-medium">Connected</span>
        <Badge variant="secondary">GitHub App installed</Badge>
        <ManageReposButton orgId={orgId} />
      </div>

      {isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : error ? (
        <p className="text-destructive text-sm">{error.message}</p>
      ) : repos.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No repositories synced yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {repos.map((repo) => (
            <li
              key={repo.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3"
            >
              <div className="flex items-center gap-3">
                <div className="bg-muted text-muted-foreground grid size-9 place-items-center rounded-md">
                  <GitBranch className="size-4" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {repo.owner}/{repo.name}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    Default branch: {repo.defaultBranch}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
