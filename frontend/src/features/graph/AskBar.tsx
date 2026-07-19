/**
 * The graph Q&A bar (T4.5 UI): a pill pinned bottom-right over the canvas.
 * Submitting expands it upward into an answer panel — the answer region is a
 * bottom-anchored grid row animating 0fr → 1fr, so no height measuring — while
 * the traversal animates on the graph behind it (fed by useAskGraph, not this
 * component). Done answers grow a citation-chip row; chips focus their node in
 * the graph, and `pr:` nodes add an external GitHub link.
 */
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import {
  ExternalLink,
  Loader2,
  Mic,
  SendHorizontal,
  Square,
  X,
} from "lucide-react"
import ReactMarkdown, { type Components } from "react-markdown"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import type { AskCitation, AskStatus } from "./ask-types"
import { prUrlFromNodeId } from "./ask-types"
import { useVoiceInput } from "./useVoiceInput"

interface AskBarProps {
  orgId: string | null
  status: AskStatus
  answer: string
  citations: AskCitation[]
  error: string | null
  onAsk: (question: string) => void
  onCancel: () => void
  onDismiss: () => void
  /** Focus/select the cited node in the graph (GraphPage's hop path). */
  onCitationClick: (nodeId: string) => void
  /** Label for a node in the current payload; null when filtered out/absent. */
  getNodeLabel: (nodeId: string) => string | null
}

// Markdown → the panel's compact type scale, all theme tokens (dark-mode free).
const MD_COMPONENTS: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc pl-4">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal pl-4">{children}</ol>,
  li: ({ children }) => <li className="mb-0.5">{children}</li>,
  code: ({ children }) => (
    <code className="bg-muted rounded px-1 font-mono text-xs">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="bg-muted mb-2 overflow-x-auto rounded p-2 text-xs">
      {children}
    </pre>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-primary hover:underline"
    >
      {children}
    </a>
  ),
  h1: ({ children }) => (
    <h1 className="mt-2 mb-1 text-sm font-semibold">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-2 mb-1 text-sm font-semibold">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-2 mb-1 text-sm font-semibold">{children}</h3>
  ),
}

function CitationChip({
  citation,
  label,
  onClick,
}: {
  citation: AskCitation
  label: string | null
  onClick: () => void
}) {
  const prUrl = prUrlFromNodeId(citation.nodeId)
  const text =
    label ??
    (citation.prNumber !== null
      ? `PR #${citation.prNumber}`
      : citation.nodeId.slice(0, 24))
  return (
    <Badge
      variant="outline"
      role="button"
      tabIndex={0}
      title={citation.nodeId}
      onClick={label ? onClick : undefined}
      onKeyDown={(e) => {
        if (label && (e.key === "Enter" || e.key === " ")) onClick()
      }}
      className={cn(
        "max-w-48 gap-1",
        label ? "hover:bg-accent cursor-pointer" : "opacity-50",
      )}
    >
      <span className="truncate">{text}</span>
      {prUrl && (
        <a
          href={prUrl}
          target="_blank"
          rel="noreferrer"
          aria-label="Open pull request on GitHub"
          onClick={(e) => e.stopPropagation()}
          className="text-muted-foreground hover:text-foreground shrink-0"
        >
          <ExternalLink className="size-3" />
        </a>
      )}
    </Badge>
  )
}

export function AskBar({
  orgId,
  status,
  answer,
  citations,
  error,
  onAsk,
  onCancel,
  onDismiss,
  onCitationClick,
  getNodeLabel,
}: AskBarProps) {
  const [question, setQuestion] = useState("")
  const lastQuestionRef = useRef("")
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const streaming = status === "streaming"
  const expanded = status !== "idle"

  const voice = useVoiceInput({
    orgId,
    // Voice augments the box rather than replacing typed text; the user still
    // submits with Enter.
    onTranscript: (text) =>
      setQuestion((q) => (q.trim() ? `${q.trim()} ${text}` : text)),
    onError: (message) => toast.error(message),
  })
  const recording = voice.status === "recording"
  const transcribing = voice.status === "transcribing"

  // Keep the newest text in view while it streams in.
  useEffect(() => {
    if (streaming) bottomRef.current?.scrollIntoView({ block: "nearest" })
  }, [answer, streaming])

  const submit = () => {
    const trimmed = question.trim()
    if (!trimmed || streaming) return
    lastQuestionRef.current = trimmed
    onAsk(trimmed)
    setQuestion("")
  }

  return (
    <div
      className="absolute right-4 bottom-4 z-10 w-[400px] max-w-[calc(100%-2rem)]"
      onKeyDown={(e) => {
        if (e.key !== "Escape") return
        if (streaming) onCancel()
        else onDismiss()
      }}
    >
      <div className="bg-card/90 overflow-hidden rounded-lg border shadow-lg backdrop-blur">
        {/* Answer region: bottom-anchored, so growing 0fr → 1fr expands upward. */}
        <div
          className={cn(
            "grid transition-[grid-template-rows] duration-300 ease-out",
            expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="flex items-center justify-between gap-2 border-b px-3 py-1.5">
              <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
                {streaming && (
                  <>
                    <Loader2 className="size-3 animate-spin" /> Thinking…
                  </>
                )}
                {status === "done" && "Answer"}
                {status === "error" && "Something went wrong"}
              </span>
              {streaming ? (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={onCancel}
                  aria-label="Stop"
                >
                  <Square />
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={onDismiss}
                  aria-label="Close"
                >
                  <X />
                </Button>
              )}
            </div>

            <ScrollArea className="max-h-[45vh] [&>[data-slot=scroll-area-viewport]]:max-h-[45vh]">
              <div className="px-3 py-2 text-sm">
                {error && <p className="text-destructive">{error}</p>}
                {answer ? (
                  <ReactMarkdown components={MD_COMPONENTS}>
                    {answer}
                  </ReactMarkdown>
                ) : status === "done" && !error ? (
                  <p className="text-muted-foreground">No answer produced.</p>
                ) : null}
                {status === "error" && lastQuestionRef.current && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => onAsk(lastQuestionRef.current)}
                  >
                    Retry
                  </Button>
                )}
                {status === "done" && citations.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5 border-t pt-2">
                    {citations.map((c) => (
                      <CitationChip
                        key={c.nodeId}
                        citation={c}
                        label={getNodeLabel(c.nodeId)}
                        onClick={() => onCitationClick(c.nodeId)}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            </ScrollArea>
          </div>
        </div>

        {/* Input pill — the whole bar when collapsed. */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="flex items-center gap-1.5 p-1.5"
        >
          {transcribing ? (
            <div
              className="text-muted-foreground flex h-8 flex-1 items-center gap-2 px-2.5 text-sm"
              aria-live="polite"
            >
              <Loader2 className="size-4 animate-spin" />
              <span>Transcribing…</span>
            </div>
          ) : (
            <Input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={recording ? "Listening…" : "Ask about this graph…"}
              disabled={streaming}
              aria-label="Ask a question about the graph"
              className="h-8 border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
          )}
          {!voice.unsupported && (
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              onClick={voice.toggle}
              disabled={streaming || transcribing}
              aria-label={recording ? "Stop recording" : "Ask by voice"}
              className={cn(recording && "text-destructive")}
            >
              {recording ? <Square /> : <Mic />}
            </Button>
          )}
          <Button
            type="submit"
            size="icon-sm"
            variant="ghost"
            disabled={streaming || transcribing || !question.trim()}
            aria-label="Ask"
          >
            <SendHorizontal />
          </Button>
        </form>
      </div>
    </div>
  )
}
