/**
 * Roster of every member in the active org. Read-only; role comes from the
 * embedded membership, not the free-text `User.role`.
 */
import { useOrgMembers } from "@/lib/api"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

function initials(name: string | null, email: string): string {
  return (name || email).slice(0, 2).toUpperCase()
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

export function MembersTable({ orgId }: { orgId: string }) {
  const { data: members, isPending, error } = useOrgMembers(orgId)

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Member</TableHead>
          <TableHead>Email</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Joined</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isPending ? (
          Array.from({ length: 3 }).map((_, i) => (
            <TableRow key={i}>
              <TableCell colSpan={4}>
                <Skeleton className="h-8 w-full" />
              </TableCell>
            </TableRow>
          ))
        ) : error ? (
          <TableRow>
            <TableCell colSpan={4} className="text-destructive text-sm">
              {error.message}
            </TableCell>
          </TableRow>
        ) : (
          members?.map((m) => (
            <TableRow key={m.user.id}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <Avatar className="size-7">
                    <AvatarFallback className="text-xs">
                      {initials(m.user.name, m.user.email)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="font-medium">
                    {m.user.name}
                  </span>
                </div>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {m.user.email}
              </TableCell>
              <TableCell>
                <Badge variant={m.role === "admin" ? "default" : "secondary"}>
                  {m.role}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatDate(m.joinedAt)}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )
}
