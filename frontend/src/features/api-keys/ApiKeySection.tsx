/**
 * API key management for the active org. Enforces a single key per user per org:
 * when none exists it offers generation, and when one exists it shows metadata
 * plus regeneration. Freshly created/regenerated secrets are surfaced once via
 * {@link ApiKeyReveal}.
 */
import { useState, type FormEvent } from "react"
import { KeyRound, Loader2, Plus } from "lucide-react"
import { toast } from "sonner"

import {
  type ApiKeyCreated,
  useApiKeys,
  useCreateApiKey,
} from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ApiKeyReveal } from "./ApiKeyReveal"
import { RegenerateKeyButton } from "./RegenerateKeyButton"

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

export function ApiKeySection() {
  const { orgId } = useActiveOrg()
  const { data: keys, isPending, error } = useApiKeys()
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null)

  // Single-key model: use the first (newest) key scoped to the active org.
  const apiKey = keys?.find((k) => k.orgId === orgId)

  return (
    <Card>
      <CardHeader>
        <CardTitle>API key</CardTitle>
        <CardDescription>
          Authenticate the CLI and integrations with a personal key for this org.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : error ? (
          <p className="text-destructive text-sm">{error.message}</p>
        ) : apiKey ? (
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border p-4">
            <div className="flex items-center gap-3">
              <div className="bg-muted text-muted-foreground grid size-9 place-items-center rounded-md">
                <KeyRound className="size-4" />
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{apiKey.label}</p>
                <p className="text-muted-foreground text-xs">
                  Created {formatDate(apiKey.createdAt)} ·{" "}
                  {apiKey.lastUsedAt ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span>Last used {formatDate(apiKey.lastUsedAt)}</span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {new Date(apiKey.lastUsedAt).toLocaleString()}
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <span>Never used</span>
                  )}
                </p>
              </div>
            </div>
            <RegenerateKeyButton keyId={apiKey.id} onRegenerated={setRevealed} />
          </div>
        ) : (
          <EmptyState orgId={orgId} onCreated={setRevealed} />
        )}
      </CardContent>

      <ApiKeyReveal created={revealed} onClose={() => setRevealed(null)} />
    </Card>
  )
}

function EmptyState({
  orgId,
  onCreated,
}: {
  orgId: string
  onCreated: (created: ApiKeyCreated) => void
}) {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState("")
  const createKey = useCreateApiKey({
    onSuccess: (created) => {
      setOpen(false)
      setLabel("")
      onCreated(created)
    },
    onError: (err) => toast.error(err.message || "Couldn't create the key."),
  })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = label.trim()
    if (!trimmed) return
    // Always pass orgId explicitly — avoids the multi-org selection ambiguity.
    createKey.mutate({ label: trimmed, orgId })
  }

  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed p-6 text-center">
      <div className="bg-muted text-muted-foreground grid size-10 place-items-center rounded-full">
        <KeyRound className="size-5" />
      </div>
      <div>
        <p className="text-sm font-medium">No API key yet</p>
        <p className="text-muted-foreground text-sm">
          Generate one to start using the API.
        </p>
      </div>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (createKey.isPending) return
          setOpen(next)
        }}
      >
        <DialogTrigger asChild>
          <Button>
            <Plus />
            Generate API key
          </Button>
        </DialogTrigger>
        <DialogContent>
          <form onSubmit={onSubmit}>
            <DialogHeader>
              <DialogTitle>Generate API key</DialogTitle>
              <DialogDescription>
                Give it a name so you can recognize it later.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-2 py-4">
              <Label htmlFor="key-label">Key name</Label>
              <Input
                id="key-label"
                placeholder="e.g. Laptop CLI"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div className="mt-4 flex justify-end">
              <Button
                type="submit"
                disabled={createKey.isPending || !label.trim()}
              >
                {createKey.isPending && <Loader2 className="animate-spin" />}
                Generate
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
