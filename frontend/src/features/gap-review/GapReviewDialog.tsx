/**
 * Gap review: a blocking, non-dismissible modal that appears whenever the org
 * has open gap chats — the questions raised when an uploaded doc's claim looks
 * out of date against the current code (see app/file_upload/gap_detection.py and
 * app/gap_chat). The engineer must answer every open question (typed or by
 * voice) before they can get back to the app; each answer resolves a memory to
 * verified or superseded, and once the queue empties the knowledge graph is
 * refreshed so the new/updated memory nodes appear.
 *
 * Mounted once in the app shell. It polls the open-chat list so questions
 * surface shortly after a document finishes enriching.
 */
import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FileText, Loader2, Mic, SendHorizontal, Square } from "lucide-react"

import {
  queryKeys,
  useAnswerGapChat,
  useAnswerGapChatAudio,
  useDocuments,
  useGapChats,
  type GapChat,
  type ObjectId,
} from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { useAudioRecorder } from "./useAudioRecorder"
import { useUploadSignal } from "./upload-signal"

const REVIEW_POLL_MS = 5000
// Safety cap: if enrichment never reports back (e.g. the server restarted mid
// background job), stop showing the spinner after this long so it can't hang.
const MAX_PROCESSING_MS = 120_000
// Grace covering the window between upload and the first docs refetch, so the
// spinner shows immediately — before the freshly-uploaded doc (marked
// `enriching`) has appeared in the polled list.
const INITIAL_GRACE_MS = 6_000

/** The bare claim, prefix-stripped and clipped for a list row. */
function shortLabel(content: string): string {
  const stripped = content.replace(/^\[repo:[^\]]*\]\s*/, "").trim()
  return stripped.length > 64 ? `${stripped.slice(0, 63)}…` : stripped
}

/** Re-render on a timer while `active`, so elapsed-time checks stay current. */
function useNow(active: boolean, intervalMs = 1500): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [active, intervalMs])
  return now
}

export function GapReviewDialog() {
  const { orgId } = useActiveOrg()
  const qc = useQueryClient()
  const { lastUploadAt, clearUpload } = useUploadSignal()
  const { data } = useGapChats(orgId, "open", {
    refetchInterval: REVIEW_POLL_MS,
  })
  const chats = data ?? []

  // Are we still waiting on questions from a just-uploaded doc? Poll the doc list
  // so we can watch its enrichment phase. The indexing `status` settles almost
  // immediately, but gap detection runs in a separate background job tracked by
  // `enrichmentStatus` — wait on that, not on indexing, so the spinner matches
  // when questions can actually appear.
  const awaiting = lastUploadAt != null && chats.length === 0
  const now = useNow(awaiting)
  const elapsed = lastUploadAt != null ? now - lastUploadAt : Infinity
  const { data: docs } = useDocuments(orgId, {
    refetchInterval: awaiting ? 3000 : false,
  })
  const enriching = (docs ?? []).some((d) => d.enrichmentStatus === "enriching")
  // Settled once enrichment is no longer running — held off for a brief initial
  // grace so a stale (pre-upload) doc list can't report "settled" before the new
  // doc's `enriching` status has been fetched.
  const settled = elapsed > INITIAL_GRACE_MS && !enriching
  const processing = awaiting && elapsed < MAX_PROCESSING_MS && !settled

  // When enrichment wraps up, pull the open-chat list right away instead of
  // waiting for the next poll, so the dialog switches straight from the spinner
  // to the questions (or closes cleanly when there were no gaps).
  const wasEnrichingRef = useRef(false)
  useEffect(() => {
    if (enriching) {
      wasEnrichingRef.current = true
    } else if (wasEnrichingRef.current) {
      wasEnrichingRef.current = false
      qc.invalidateQueries({ queryKey: queryKeys.gapChats.all })
    }
  }, [enriching, qc])

  // Retire the upload signal once questions arrive or the wait is over, so the
  // spinner doesn't linger (and can't reappear after the queue is answered).
  useEffect(() => {
    if (lastUploadAt == null) return
    if (chats.length > 0) {
      clearUpload()
      return
    }
    if (elapsed >= MAX_PROCESSING_MS || settled) {
      clearUpload()
      // Enrichment may have written new decision nodes even when nothing
      // conflicted — refresh the graph so they show without the 60s wait.
      qc.invalidateQueries({ queryKey: queryKeys.graph.all })
      // Explicit outcome: how many memories the just-finished doc produced.
      const done = (docs ?? [])
        .filter((d) => d.enrichmentStatus === "done")
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]
      if (done) {
        toast.success(
          done.decisionsWritten > 0
            ? `Added ${done.decisionsWritten} ${
                done.decisionsWritten === 1 ? "memory node" : "memory nodes"
              } to the graph.`
            : "No decisions were found in your documents.",
        )
      }
    }
  }, [chats.length, elapsed, settled, lastUploadAt, clearUpload, qc, docs])

  const [selectedId, setSelectedId] = useState<ObjectId | null>(null)
  // The selection follows the list: keep the chosen chat, else fall to the
  // first still-open one (so answering auto-advances to the next question).
  const selected = chats.find((c) => c.id === selectedId) ?? chats[0] ?? null
  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id)
  }, [selected, selectedId])

  // When the queue drains after having had questions, the graph now holds new
  // verified/superseded nodes — pull it fresh and tell the user we're done.
  const hadOpenRef = useRef(false)
  useEffect(() => {
    if (chats.length > 0) {
      hadOpenRef.current = true
      return
    }
    if (hadOpenRef.current) {
      hadOpenRef.current = false
      qc.invalidateQueries({ queryKey: queryKeys.graph.all })
      toast.success("All questions answered — the graph has been updated.")
    }
  }, [chats.length, qc])

  if (!selected && !processing) return null

  return (
    <Dialog open>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        className="sm:max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle>Reconcile your docs with the code</DialogTitle>
          <DialogDescription>
            {selected
              ? "These claims from your uploaded docs look out of date against the current code. Answer each one to update the knowledge graph — this stays open until they're all resolved."
              : "We're checking your uploaded documents against the codebase for anything that looks out of date."}
          </DialogDescription>
        </DialogHeader>

        {!selected ? (
          <div className="flex h-[26rem] items-center justify-center">
            <div className="flex items-center gap-3 text-muted-foreground">
              <Loader2 className="size-5 animate-spin" />
              <span className="text-sm">Processing your documents…</span>
            </div>
          </div>
        ) : (
        <div className="grid h-[26rem] grid-cols-[14rem_1fr] gap-4 overflow-hidden">
          <div className="flex min-h-0 flex-col rounded-lg border">
            <div className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">
              {chats.length} to review
            </div>
            <ScrollArea className="min-h-0 flex-1">
              <ul className="p-1.5">
                {chats.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(c.id)}
                      className={cn(
                        "w-full rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                        c.id === selected.id
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                      )}
                    >
                      <span className="line-clamp-2">
                        {shortLabel(c.memoryContent)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </ScrollArea>
          </div>

          <GapReviewDetail key={selected.id} orgId={orgId} chat={selected} />
        </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function GapReviewDetail({ orgId, chat }: { orgId: ObjectId; chat: GapChat }) {
  const [text, setText] = useState("")

  const onResolved = (resolution: "verified" | "superseded") => {
    toast.success(
      resolution === "verified"
        ? "Confirmed — the docs still hold."
        : "Recorded the update — the old memory was superseded.",
    )
  }

  const typed = useAnswerGapChat(orgId, chat.id, {
    onSuccess: (r) => {
      setText("")
      onResolved(r.resolution)
    },
    onError: (e) => toast.error(e.message),
  })
  const spoken = useAnswerGapChatAudio(orgId, chat.id, {
    onSuccess: (r) => onResolved(r.resolution),
    onError: (e) => toast.error(e.message),
  })
  const recorder = useAudioRecorder({
    onAudio: (file) => spoken.mutate(file),
    onError: (m) => toast.error(m),
  })

  const recording = recorder.status === "recording"
  const busy = typed.isPending || spoken.isPending
  const question = chat.messages[0]?.text ?? shortLabel(chat.memoryContent)

  const submit = () => {
    const answer = text.trim()
    if (!answer || busy) return
    typed.mutate({ answer })
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <ScrollArea className="min-h-0 flex-1 rounded-lg border p-3">
        <p className="text-sm whitespace-pre-wrap">{question}</p>
        {chat.changedFiles.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {chat.changedFiles.map((f) => (
              <Badge key={f} variant="outline" className="gap-1 font-mono text-xs">
                <FileText className="size-3" />
                {f}
              </Badge>
            ))}
          </div>
        )}
      </ScrollArea>

      <div className="flex items-end gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              submit()
            }
          }}
          disabled={busy || recording}
          placeholder={
            recording ? "Listening… tap the mic to finish" : "Type your answer…"
          }
          className="min-h-16 flex-1 resize-none"
          aria-label="Your answer"
        />
        <div className="flex flex-col gap-2">
          {!recorder.unsupported && (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              onClick={recorder.toggle}
              disabled={busy}
              aria-label={recording ? "Stop recording" : "Answer by voice"}
              className={cn(recording && "text-destructive")}
            >
              {recording ? <Square /> : <Mic />}
            </Button>
          )}
          <Button
            type="button"
            size="icon"
            onClick={submit}
            disabled={busy || recording || !text.trim()}
            aria-label="Submit answer"
          >
            {busy ? <Loader2 className="animate-spin" /> : <SendHorizontal />}
          </Button>
        </div>
      </div>
      {(busy || recording) && (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {spoken.isPending
            ? "Transcribing and resolving…"
            : typed.isPending
              ? "Resolving…"
              : "Listening…"}
        </p>
      )}
    </div>
  )
}
