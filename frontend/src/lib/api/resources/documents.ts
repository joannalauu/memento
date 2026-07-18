/**
 * Documents — `app/file_upload/routes.py` (mounted at `/documents`).
 *
 * Upload is multipart (`file` + optional `repo_id`). Indexing is async, so the
 * list/detail hooks accept a `refetchInterval` to poll while a document is
 * `pending`/`processing`.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
} from "@tanstack/react-query"

import { request } from "../http"
import { queryKeys } from "../query-keys"
import type { Document, ObjectId } from "../types"

export interface UploadDocumentInput {
  file: File
  /** Scope the doc to a repo to trigger background anchor enrichment. */
  repoId?: ObjectId
}

export const documentsApi = {
  /** `GET /documents/{org_id}` — org docs, newest first, live status merged. */
  list: (orgId: ObjectId, signal?: AbortSignal) =>
    request<Document[]>(`/documents/${orgId}`, { signal }),

  /** `GET /documents/{org_id}/{doc_id}`. */
  get: (orgId: ObjectId, docId: ObjectId, signal?: AbortSignal) =>
    request<Document>(`/documents/${orgId}/${docId}`, { signal }),

  /** `POST /documents/{org_id}` — multipart upload. */
  upload: (orgId: ObjectId, input: UploadDocumentInput) => {
    const form = new FormData()
    form.append("file", input.file)
    if (input.repoId) form.append("repo_id", input.repoId)
    return request<Document>(`/documents/${orgId}`, {
      method: "POST",
      body: form,
    })
  },

  /** `DELETE /documents/{org_id}/{doc_id}`. */
  remove: (orgId: ObjectId, docId: ObjectId) =>
    request<void>(`/documents/${orgId}/${docId}`, { method: "DELETE" }),
}

export function useDocuments(
  orgId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<Document[]>>,
) {
  return useQuery({
    queryKey: queryKeys.documents.list(orgId ?? ""),
    queryFn: ({ signal }) => documentsApi.list(orgId as ObjectId, signal),
    enabled: !!orgId,
    ...options,
  })
}

export function useDocument(
  orgId: ObjectId | undefined,
  docId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<Document>>,
) {
  return useQuery({
    queryKey: queryKeys.documents.detail(orgId ?? "", docId ?? ""),
    queryFn: ({ signal }) =>
      documentsApi.get(orgId as ObjectId, docId as ObjectId, signal),
    enabled: !!orgId && !!docId,
    ...options,
  })
}

export function useUploadDocument(
  orgId: ObjectId,
  options?: UseMutationOptions<Document, Error, UploadDocumentInput>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: UploadDocumentInput) =>
      documentsApi.upload(orgId, input),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.documents.list(orgId) })
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useDeleteDocument(
  orgId: ObjectId,
  options?: UseMutationOptions<void, Error, ObjectId>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (docId: ObjectId) => documentsApi.remove(orgId, docId),
    ...options,
    onSuccess: (data, docId, ...rest) => {
      qc.removeQueries({ queryKey: queryKeys.documents.detail(orgId, docId) })
      qc.invalidateQueries({ queryKey: queryKeys.documents.list(orgId) })
      options?.onSuccess?.(data, docId, ...rest)
    },
  })
}
