/**
 * Authenticated app shell: header (brand, org selector, nav, theme, account
 * menu) plus the routed page body. Loads the user + orgs, then provides the
 * active-org context to everything below. A signed-in user with no org is sent
 * to onboarding.
 */
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom"
import { Home, LogOut, Waypoints } from "lucide-react"

import { logout, useMe, useMyOrgs, type User } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Avatar,
  AvatarFallback,
} from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { FullPageError, FullPageSpinner } from "@/components/full-page-state"
import { OrgProvider, useActiveOrg } from "./org-context"
import { ThemeToggle } from "./ThemeToggle"

export function AppShell() {
  const me = useMe({ retry: false })
  const orgs = useMyOrgs({ retry: false, enabled: !!me.data })

  if (me.isPending || (me.data && orgs.isPending)) return <FullPageSpinner />
  if (me.error || !me.data) return <FullPageError message={me.error?.message} />
  if (orgs.error) return <FullPageError message={orgs.error.message} />
  if (!orgs.data?.length) return <Navigate to="/onboarding" replace />

  return (
    <OrgProvider orgs={orgs.data} me={me.data}>
      <div className="bg-background text-foreground flex h-screen flex-col">
        <ShellHeader me={me.data} />
        <div className="min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </div>
      </div>
    </OrgProvider>
  )
}

function ShellHeader({ me }: { me: User }) {
  const { org, orgs, role, setOrgId } = useActiveOrg()
  const homePath = role === "admin" ? "/admin" : "/home"

  return (
    <header className="flex items-center justify-between gap-4 border-b px-4 py-2">
      <div className="flex items-center gap-3">
        <ContextualNavButton homePath={homePath} />

        {orgs.length > 1 ? (
          <Select value={org.id} onValueChange={setOrgId}>
            <SelectTrigger size="sm" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {orgs.map((o) => (
                <SelectItem key={o.id} value={o.id}>
                  {o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <span className="text-muted-foreground text-sm">{org.name}</span>
        )}

      </div>

      <div className="flex items-center gap-1">
        <ThemeToggle />
        <AccountMenu me={me} />
      </div>
    </header>
  )
}

/**
 * Single top-left toggle between the dashboard and the graph: shows "Memory
 * Graph" on the home pages and "Home" while on the graph, always in the same
 * spot. Home routes to admin or member home per the caller's role.
 */
function ContextualNavButton({ homePath }: { homePath: string }) {
  const location = useLocation()
  const navigate = useNavigate()
  const onGraph = location.pathname === "/graph"

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => navigate(onGraph ? homePath : "/graph")}
    >
      {onGraph ? <Home /> : <Waypoints />}
      {onGraph ? "Home" : "Memory Graph"}
    </Button>
  )
}

function AccountMenu({ me }: { me: User }) {
  const label = me.name || me.email
  const initials = (me.name || me.email).slice(0, 2).toUpperCase()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Account menu">
          <Avatar className="size-7">
            <AvatarFallback className="text-xs">{initials}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="truncate text-sm font-medium">{label}</span>
          <span className="text-muted-foreground truncate text-xs font-normal">
            {me.email}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => logout()}>
          <LogOut />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
