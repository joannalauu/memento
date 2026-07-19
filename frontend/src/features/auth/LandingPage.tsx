/**
 * Signed-out landing page. Both actions kick off the same Auth0 flow — the
 * hosted login page offers Google and GitHub — so "sign up" and "log in" differ
 * only in framing. New admins land on onboarding after auth; returning users are
 * routed to their home by the root dispatcher.
 */
import { Waypoints } from "lucide-react"

import { login } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ThemeToggle } from "@/components/app-shell/ThemeToggle"

export function LandingPage() {
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
          <CardTitle className="text-xl">Memento</CardTitle>
          <CardDescription>
            The living knowledge graph for your engineering org.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Button size="lg" onClick={login}>
            Sign up
          </Button>
          <Button size="lg" variant="outline" onClick={login}>
            Log in
          </Button>
          <p className="text-muted-foreground text-center text-xs">
            Continue with Google or GitHub.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
