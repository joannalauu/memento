/**
 * Invite a new member by email. The backend emails the invitee an accept link
 * (best-effort), so on success we also surface the same link here with a copy
 * button — covering email-delivery failures and letting an admin share it
 * directly.
 */
import { useState, type FormEvent } from "react"
import { Check, Copy, Loader2, UserPlus } from "lucide-react"
import { toast } from "sonner"

import { useCreateOrgInvite } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

function acceptLink(token: string): string {
  // Points at the SPA join-org page — the same link the invite email uses.
  return `${window.location.origin}/join-org?token=${token}`
}

export function InviteMemberDialog({ orgId }: { orgId: string }) {
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState("")
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const invite = useCreateOrgInvite(orgId, {
    onSuccess: (created) => {
      toast.success(`Invite sent to ${email}`)
      setLink(acceptLink(created.token))
    },
    onError: (err) => toast.error(err.message || "Couldn't send the invite."),
  })

  const reset = () => {
    setEmail("")
    setLink(null)
    setCopied(false)
    invite.reset()
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = email.trim()
    if (!trimmed) return
    invite.mutate({ email: trimmed })
  }

  const copy = async () => {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Couldn't copy — select the link and copy it manually.")
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (invite.isPending) return
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <UserPlus />
          Invite member
        </Button>
      </DialogTrigger>
      <DialogContent>
        {link ? (
          <>
            <DialogHeader>
              <DialogTitle>Invite sent</DialogTitle>
              <DialogDescription>
                We emailed {email} an invite link. You can also share it directly.
              </DialogDescription>
            </DialogHeader>
            <div className="flex min-w-0 items-center gap-2">
              <code className="bg-muted text-foreground min-w-0 flex-1 overflow-x-auto rounded-md border px-3 py-2 font-mono text-xs whitespace-nowrap">
                {link}
              </code>
              <Button
                variant="outline"
                size="icon"
                onClick={copy}
                aria-label="Copy invite link"
              >
                {copied ? <Check className="text-green-600" /> : <Copy />}
              </Button>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={reset}>
                Invite another
              </Button>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </div>
          </>
        ) : (
          <form onSubmit={onSubmit}>
            <DialogHeader>
              <DialogTitle>Invite a member</DialogTitle>
              <DialogDescription>
                They'll join as a member once they sign in with this email.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-2 py-4">
              <Label htmlFor="invite-email">Email address</Label>
              <Input
                id="invite-email"
                type="email"
                placeholder="teammate@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div className="mt-4 flex justify-end">
              <Button
                type="submit"
                disabled={invite.isPending || !email.trim()}
              >
                {invite.isPending && <Loader2 className="animate-spin" />}
                Send invite
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
