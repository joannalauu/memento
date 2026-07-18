/**
 * Admin dashboard: the org's member roster with invites, plus API key
 * management. Members who aren't admins are redirected to their own home.
 */
import { Navigate } from "react-router-dom"

import { useActiveOrg } from "@/components/app-shell/org-context"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ApiKeySection } from "@/features/api-keys/ApiKeySection"
import { InviteMemberDialog } from "./InviteMemberDialog"
import { MembersTable } from "./MembersTable"

export function AdminHomePage() {
  const { org, orgId, role } = useActiveOrg()

  if (role !== "admin") return <Navigate to="/home" replace />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-6">
        <div>
          <h1 className="text-xl font-semibold">{org.name}</h1>
          <p className="text-muted-foreground text-sm">
            Manage your organization's members and access.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Members</CardTitle>
            <CardDescription>
              Everyone with access to this organization.
            </CardDescription>
            <CardAction>
              <InviteMemberDialog orgId={orgId} />
            </CardAction>
          </CardHeader>
          <CardContent>
            <MembersTable orgId={orgId} />
          </CardContent>
        </Card>

        <ApiKeySection />
      </div>
    </div>
  )
}
