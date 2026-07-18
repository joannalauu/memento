/**
 * Regenerate confirmation. Overwriting a key's secret invalidates the old one
 * immediately, so this gates the action behind an explicit confirm before
 * handing the new secret up for a one-time reveal.
 */
import { useState } from "react"
import { Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { type ApiKeyCreated, type ObjectId, useRegenerateApiKey } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

interface RegenerateKeyButtonProps {
  keyId: ObjectId
  onRegenerated: (created: ApiKeyCreated) => void
}

export function RegenerateKeyButton({
  keyId,
  onRegenerated,
}: RegenerateKeyButtonProps) {
  const [open, setOpen] = useState(false)
  const regenerate = useRegenerateApiKey({
    onSuccess: (created) => {
      setOpen(false)
      onRegenerated(created)
    },
    onError: (error) =>
      toast.error(error.message || "Couldn't regenerate the key."),
  })

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Don't let a click-out close the dialog mid-request.
        if (regenerate.isPending) return
        setOpen(next)
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          <RefreshCw />
          Regenerate
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Regenerate API key?</AlertDialogTitle>
          <AlertDialogDescription>
            The current key stops working immediately. Any integration using it
            will need to be updated with the new key.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={regenerate.isPending}>
            Cancel
          </AlertDialogCancel>
          <Button
            variant="destructive"
            onClick={() => regenerate.mutate(keyId)}
            disabled={regenerate.isPending}
          >
            {regenerate.isPending && <Loader2 className="animate-spin" />}
            Regenerate
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
