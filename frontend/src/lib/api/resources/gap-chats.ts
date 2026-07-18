/**
 * Gap chats — `app/gap_chat/routes.py` (mounted at `/gap-chats`).
 *
 * A gap chat is the verification loop between a legacy memory and the code that
 * moved past it. Answering (typed or by voice) resolves the memory to
 * `verified` or `superseded` and returns an {@link AnswerResult}.
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
import type {
  AnswerRequest,
  AnswerResult,
  GapChat,
  GapChatStatus,
  ObjectId,
} from "../types"

export const gapChatsApi = {
  /** `GET /gap-chats/{org_id}` — optionally filtered by status, newest first. */
  list: (orgId: ObjectId, status?: GapChatStatus, signal?: AbortSignal) =>
    request<GapChat[]>(`/gap-chats/${orgId}`, {
      signal,
      params: { status_filter: status },
    }),

  /** `GET /gap-chats/{org_id}/{chat_id}`. */
  get: (orgId: ObjectId, chatId: ObjectId, signal?: AbortSignal) =>
    request<GapChat>(`/gap-chats/${orgId}/${chatId}`, { signal }),

  /** `POST /gap-chats/{org_id}/{chat_id}/answer` — typed answer. */
  answer: (orgId: ObjectId, chatId: ObjectId, body: AnswerRequest) =>
    request<AnswerResult>(`/gap-chats/${orgId}/${chatId}/answer`, {
      method: "POST",
      body,
    }),

  /** `POST /gap-chats/{org_id}/{chat_id}/answer/audio` — voice answer. */
  answerAudio: (orgId: ObjectId, chatId: ObjectId, file: File) => {
    const form = new FormData()
    form.append("file", file)
    return request<AnswerResult>(
      `/gap-chats/${orgId}/${chatId}/answer/audio`,
      { method: "POST", body: form },
    )
  },
}

export function useGapChats(
  orgId: ObjectId | undefined,
  status?: GapChatStatus,
  options?: Partial<UseQueryOptions<GapChat[]>>,
) {
  return useQuery({
    queryKey: queryKeys.gapChats.list(orgId ?? "", status),
    queryFn: ({ signal }) =>
      gapChatsApi.list(orgId as ObjectId, status, signal),
    enabled: !!orgId,
    ...options,
  })
}

export function useGapChat(
  orgId: ObjectId | undefined,
  chatId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<GapChat>>,
) {
  return useQuery({
    queryKey: queryKeys.gapChats.detail(orgId ?? "", chatId ?? ""),
    queryFn: ({ signal }) =>
      gapChatsApi.get(orgId as ObjectId, chatId as ObjectId, signal),
    enabled: !!orgId && !!chatId,
    ...options,
  })
}

/** Invalidate a chat's detail + every list scope for its org after resolving. */
function invalidateGapChat(
  qc: ReturnType<typeof useQueryClient>,
  orgId: ObjectId,
  chatId: ObjectId,
) {
  qc.invalidateQueries({ queryKey: queryKeys.gapChats.detail(orgId, chatId) })
  qc.invalidateQueries({ queryKey: queryKeys.gapChats.lists() })
}

export function useAnswerGapChat(
  orgId: ObjectId,
  chatId: ObjectId,
  options?: UseMutationOptions<AnswerResult, Error, AnswerRequest>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AnswerRequest) =>
      gapChatsApi.answer(orgId, chatId, body),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      invalidateGapChat(qc, orgId, chatId)
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useAnswerGapChatAudio(
  orgId: ObjectId,
  chatId: ObjectId,
  options?: UseMutationOptions<AnswerResult, Error, File>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => gapChatsApi.answerAudio(orgId, chatId, file),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      invalidateGapChat(qc, orgId, chatId)
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}
