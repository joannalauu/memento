/**
 * One-time reveal of a freshly minted API key secret. Shown after create or
 * regenerate — the raw key is never retrievable again, so this dialog resists
 * accidental dismissal (no outside-click / escape close) and offers copy-to-
 * clipboard before the user explicitly closes it.
 */
import { useState } from "react"
import { Check, Copy, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import type { ApiKeyCreated } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface ApiKeyRevealProps {
  /** The created/regenerated key, or null when nothing is being revealed. */
  created: ApiKeyCreated | null
  onClose: () => void
}

export function ApiKeyReveal({ created, onClose }: ApiKeyRevealProps) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.key)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Couldn't copy — select the key and copy it manually.")
    }
  }

  return (
    <Dialog
      open={!!created}
      onOpenChange={(open) => {
        if (!open) {
          setCopied(false)
          onClose()
        }
      }}
    >
      <DialogContent
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
        showCloseButton={false}
      >
        <DialogHeader>
          <DialogTitle>Your API key</DialogTitle>
          <DialogDescription>
            Copy it now — for security, it won't be shown again.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <code className="bg-muted text-foreground min-w-0 flex-1 truncate rounded-md border px-3 py-2 font-mono text-sm">
            {created?.key}
          </code>
          <Button
            variant="outline"
            size="icon"
            onClick={copy}
            aria-label="Copy API key"
          >
            {copied ? <Check className="text-green-600" /> : <Copy />}
          </Button>
        </div>

        <div className="text-muted-foreground flex items-start gap-2 text-xs">
          <TriangleAlert className="text-destructive mt-0.5 size-3.5 shrink-0" />
          <span>
            Store this somewhere safe. Anyone with this key can access your org's
            data. If you lose it, regenerate a new one.
          </span>
        </div>

        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
