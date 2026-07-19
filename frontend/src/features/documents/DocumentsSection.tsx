/**
 * Legacy document upload for the active org (admin-only surface — rendered from
 * the admin dashboard, which non-admins can't reach). A single click-to-browse
 * box uploads a file; the list below shows each doc's indexing status and polls
 * while anything is still processing.
 */
import { useRef, type ChangeEvent } from "react"
import { FileText, Loader2, Upload } from "lucide-react"
import { toast } from "sonner"

import { type Document, useDocuments, useUploadDocument } from "@/lib/api"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

function statusBadge(status: Document["status"]) {
  switch (status) {
    case "indexed":
      return <Badge>Indexed</Badge>
    case "error":
      return <Badge variant="destructive">Error</Badge>
    default:
      // pending | processing
      return (
        <Badge variant="secondary">
          <Loader2 className="animate-spin" />
          {status === "processing" ? "Processing" : "Pending"}
        </Badge>
      )
  }
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

  // Uploading requires a connected GitHub installation — the org's repos are
  // the context legacy docs get folded into, so gate the action until then.
  const connected = org.githubInstallationId != null

  const { data: docs, isPending, error } = useDocuments(orgId, {
    // Poll while any doc is still indexing so the status settles on its own.
    refetchInterval: (query) =>
      query.state.data?.some(
        (d) => d.status === "pending" || d.status === "processing",
      )
        ? 3000
        : false,
  })

  const upload = useUploadDocument(orgId, {
    onSuccess: (doc) => toast.success(`Uploaded ${doc.filename}`),
    onError: (err) => toast.error(err.message || "Upload failed."),
  })

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset the input so picking the same file again still fires onChange.
    e.target.value = ""
    if (file) upload.mutate({ file })
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
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          onChange={onPick}
          disabled={!connected || upload.isPending}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={!connected || upload.isPending}
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
            {connected
              ? "Choose a file from your computer"
              : "Connect GitHub to upload documents"}
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
                    </p>
                  </div>
                </div>
                {statusBadge(doc.status)}
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
