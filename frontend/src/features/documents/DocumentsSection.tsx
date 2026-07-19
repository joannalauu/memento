/**
 * Legacy document upload for the active org (admin-only surface — rendered from
 * the admin dashboard, which non-admins can't reach). A single click-to-browse
 * box uploads a file; the list below shows each doc's indexing status and polls
 * while anything is still processing.
 */
import { useEffect, useRef, useState, type ChangeEvent } from "react"
import { FileText, Loader2, Upload } from "lucide-react"
import { toast } from "sonner"

import {
  type Document,
  useDocuments,
  useOrgRepos,
  useUploadDocument,
} from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { useUploadSignal } from "@/features/gap-review/upload-signal"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"

function statusBadge(doc: Document) {
  if (doc.status === "error") return <Badge variant="destructive">Error</Badge>
  // Indexed into RAG, but the background enrichment + gap-detection job is still
  // running (the indexing `status` doesn't reflect it). Surface it as still
  // working so this row matches what the gap-review dialog is waiting on.
  if (doc.status === "indexed" && doc.enrichmentStatus === "enriching") {
    return (
      <Badge variant="secondary">
        <Loader2 className="animate-spin" />
        Analyzing
      </Badge>
    )
  }
  if (doc.status === "indexed") return <Badge>Indexed</Badge>
  // pending | processing
  return (
    <Badge variant="secondary">
      <Loader2 className="animate-spin" />
      {doc.status === "processing" ? "Processing" : "Pending"}
    </Badge>
  )
}

/** One-line summary of what a finished doc's enrichment produced. */
function enrichmentSummary(doc: Document): string {
  if (doc.decisionsWritten === 0) return "No decisions found"
  const decisions = `${doc.decisionsWritten} ${
    doc.decisionsWritten === 1 ? "decision" : "decisions"
  }`
  return doc.gapsOpened > 0
    ? `${decisions} · ${doc.gapsOpened} to review`
    : decisions
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

export function DocumentsSection() {
  const { org, orgId } = useActiveOrg()
  const inputRef = useRef<HTMLInputElement>(null)
  const { notifyUpload } = useUploadSignal()

  // Uploading requires a connected GitHub installation — the org's repos are
  // the context legacy docs get folded into, so gate the action until then.
  const connected = org.githubInstallationId != null

  // A doc must be scoped to a repo for the backend to enrich it (extract its
  // decisions) and detect gaps against the code — that's what raises the review
  // questions. Default to the first repo; let the user pick when there's more.
  const { data: repos } = useOrgRepos(orgId, { enabled: connected })
  const [repoId, setRepoId] = useState<string | undefined>()
  useEffect(() => {
    if (!repoId && repos?.length) setRepoId(repos[0].id)
  }, [repos, repoId])
  const activeRepoId = repoId ?? repos?.[0]?.id

  const { data: docs, isPending, error } = useDocuments(orgId, {
    // Poll while any doc is still indexing or enriching so the status settles
    // on its own (enrichment runs past "indexed" as a background job).
    refetchInterval: (query) =>
      query.state.data?.some(
        (d) =>
          d.status === "pending" ||
          d.status === "processing" ||
          d.enrichmentStatus === "enriching",
      )
        ? 3000
        : false,
  })

  const upload = useUploadDocument(orgId, {
    onSuccess: (doc) => {
      toast.success(`Uploaded ${doc.filename}`)
      // Tell the gap-review dialog to pop open with a processing spinner while
      // the backend enriches the doc and generates any review questions.
      notifyUpload()
    },
    onError: (err) => toast.error(err.message || "Upload failed."),
  })

  const canUpload = connected && !!activeRepoId && !upload.isPending

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset the input so picking the same file again still fires onChange.
    e.target.value = ""
    if (file && activeRepoId) upload.mutate({ file, repoId: activeRepoId })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Legacy documents</CardTitle>
        <CardDescription>
          Upload existing docs to fold their knowledge into shared memory.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {connected && repos && repos.length > 1 && (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-sm">Repository</span>
            <Select value={activeRepoId} onValueChange={setRepoId}>
              <SelectTrigger size="sm" className="w-64">
                <SelectValue placeholder="Select a repository" />
              </SelectTrigger>
              <SelectContent>
                {repos.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {r.owner}/{r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          // Formats we can extract decisions from for the graph. Other types
          // still upload to RAG for chat, but won't produce memory nodes.
          accept=".md,.markdown,.txt,.text,.rst,.pdf,.docx"
          onChange={onPick}
          disabled={!canUpload}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={!canUpload}
          className="border-input hover:border-primary/50 hover:bg-muted/50 focus-visible:border-ring focus-visible:ring-ring/50 flex flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors focus-visible:ring-[3px] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        >
          <div className="bg-muted text-muted-foreground grid size-10 place-items-center rounded-full">
            {upload.isPending ? (
              <Loader2 className="size-5 animate-spin" />
            ) : (
              <Upload className="size-5" />
            )}
          </div>
          <span className="text-sm font-medium">
            {upload.isPending ? "Uploading…" : "Click to upload a document"}
          </span>
          <span className="text-muted-foreground text-xs">
            {!connected
              ? "Connect GitHub to upload documents"
              : !activeRepoId
                ? "Sync a repository to upload documents"
                : "Choose a file from your computer"}
          </span>
        </button>

        {isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : error ? (
          <p className="text-destructive text-sm">{error.message}</p>
        ) : docs && docs.length > 0 ? (
          <ul className="flex flex-col divide-y rounded-lg border">
            {docs.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between gap-3 px-3 py-2.5"
              >
                <div className="flex min-w-0 items-center gap-2.5">
                  <FileText className="text-muted-foreground size-4 shrink-0" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {doc.filename}
                    </p>
                    <p className="text-muted-foreground text-xs">
                      {formatDate(doc.createdAt)}
                      {doc.enrichmentStatus === "done" &&
                        ` · ${enrichmentSummary(doc)}`}
                    </p>
                  </div>
                </div>
                {statusBadge(doc)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-center text-sm">
            No documents uploaded yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
