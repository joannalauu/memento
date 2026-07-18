/**
 * Member home: a welcome, API key management, and a jump into the knowledge
 * graph. Admins can reach this too, but they're routed to the admin dashboard
 * by default.
 */
import { Link } from "react-router-dom"
import { ArrowRight, Waypoints } from "lucide-react"

import { useMe } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import {
  Card,
  CardAction,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ApiKeySection } from "@/features/api-keys/ApiKeySection"

export function UserHomePage() {
  const { data: me } = useMe()
  const { org } = useActiveOrg()
  const greeting = me?.name ? `Welcome, ${me.name}` : "Welcome"

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-6">
        <div>
          <h1 className="text-xl font-semibold">{greeting}</h1>
          <p className="text-muted-foreground text-sm">
            You're a member of {org.name}.
          </p>
        </div>

        <Link to="/graph" className="block">
          <Card className="hover:border-primary/50 transition-colors">
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
                  <Waypoints className="size-5" />
                </div>
                <div className="space-y-1">
                  <CardTitle>Knowledge graph</CardTitle>
                  <CardDescription>
                    Explore your org's decisions, files, and features.
                  </CardDescription>
                </div>
              </div>
              <CardAction className="self-center">
                <ArrowRight className="text-muted-foreground size-5" />
              </CardAction>
            </CardHeader>
          </Card>
        </Link>

        <ApiKeySection />
      </div>
    </div>
  )
}
