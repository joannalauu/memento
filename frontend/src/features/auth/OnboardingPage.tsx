/**
 * First-run onboarding for a signed-in user with no org. Creating an org seeds
 * the creator as its first admin (server-side), so this is the "sign up as
 * admin" completion step. Users who already belong to an org are bounced back
 * to the dispatcher.
 */
import { useState, type FormEvent } from "react"
import { Navigate, useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Loader2 } from "lucide-react"

import { ApiError, useCreateOrg, useMyOrgs } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ThemeToggle } from "@/components/app-shell/ThemeToggle"
import { FullPageSpinner } from "@/components/full-page-state"

export function OnboardingPage() {
  const [name, setName] = useState("")
  const navigate = useNavigate()
  const orgs = useMyOrgs({ retry: false })
  const createOrg = useCreateOrg({
    onSuccess: (org) => {
      toast.success(`Created ${org.name}`)
      navigate("/", { replace: true })
    },
  })

  if (orgs.isPending) return <FullPageSpinner />
  // Already onboarded — don't let them create a second org from here.
  if (orgs.data && orgs.data.length > 0) return <Navigate to="/" replace />

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    createOrg.mutate({ name: trimmed })
  }

  const errorMessage =
    createOrg.error instanceof ApiError
      ? createOrg.error.message
      : createOrg.error
        ? "Could not create your organization. Please try again."
        : null

  return (
    <div className="bg-background text-foreground relative flex min-h-screen flex-col items-center justify-center p-6">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      <Card className="w-full max-w-sm">
        <form onSubmit={onSubmit}>
          <CardHeader>
            <CardTitle>Create your organization</CardTitle>
            <CardDescription>
              This is your team's workspace. You'll be its admin.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Label htmlFor="org-name">Organization name</Label>
            <Input
              id="org-name"
              placeholder="Acme Inc."
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              required
            />
            {errorMessage && (
              <p className="text-destructive text-sm">{errorMessage}</p>
            )}
          </CardContent>
          <CardFooter>
            <Button
              type="submit"
              className="w-full"
              disabled={createOrg.isPending || !name.trim()}
            >
              {createOrg.isPending && (
                <Loader2 className="animate-spin" />
              )}
              Create organization
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
