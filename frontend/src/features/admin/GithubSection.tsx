/**
 * GitHub App connection for the active org (admin-only surface — rendered from
 * the admin dashboard, which non-admins can't reach). When the org isn't yet
 * bound to an installation it offers a Connect button that redirects the browser
 * to GitHub's install page; once connected it lists the synced repositories.
 *
 * The connect flow is a full-page navigation: the returned install URL points at
 * github.com, and GitHub redirects back through the backend `/github/setup`
 * endpoint (which binds the installation and syncs repos) to
 * `GITHUB_POST_INSTALL_REDIRECT_URL`. The frontend is not involved in the
 * callback itself.
 */
import { GitBranch, Loader2 } from "lucide-react"
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
      // Full-page navigation to github.com — GitHub redirects back through the
      // backend, so we want the SPA to reload fresh in the same tab (not a new
      // tab, not a client route).
      onSuccess: ({ installUrl }) => {
        window.location.href = installUrl
      },
    })
  }

  // Stay pending through success too: the page is about to navigate away.
  const pending = connect.isPending || connect.isSuccess

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

function ConnectedState({ orgId }: { orgId: string }) {
  const { data: repos, isPending, error } = useOrgRepos(orgId)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <GitBranch className="size-4" />
        <span className="text-sm font-medium">Connected</span>
        <Badge variant="secondary">GitHub App installed</Badge>
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
