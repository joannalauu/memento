/**
 * Orgs API — `app/orgs/routes.py` (mounted at `/orgs`).
 *
 * Exposes the raw fetch functions plus React Query hooks. All endpoints are
 * member/admin-scoped server-side; the hooks surface the 403/404 as an
 * {@link ApiError}.
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
  GithubConnect,
  ObjectId,
  Org,
  OrgCreate,
  OrgInvite,
  OrgInviteCreate,
  OrgMemberRead,
  OrgUpdate,
  Repo,
} from "../types"

// ─── fetchers ────────────────────────────────────────────────────────────────

export const orgsApi = {
  /** `GET /orgs/me` — orgs the authenticated user belongs to, newest first. */
  listMine: (signal?: AbortSignal) =>
    request<Org[]>("/orgs/me", { signal }),

  /** `GET /orgs/{org_id}`. */
  get: (orgId: ObjectId, signal?: AbortSignal) =>
    request<Org>(`/orgs/${orgId}`, { signal }),

  /** `GET /orgs/{org_id}/members`. */
  listMembers: (orgId: ObjectId, signal?: AbortSignal) =>
    request<OrgMemberRead[]>(`/orgs/${orgId}/members`, { signal }),

  /** `GET /orgs/{org_id}/repos`. */
  listRepos: (orgId: ObjectId, signal?: AbortSignal) =>
    request<Repo[]>(`/orgs/${orgId}/repos`, { signal }),

  /** `GET /orgs/{org_id}/github/connect` — returns the GitHub install URL. */
  githubConnect: (orgId: ObjectId, signal?: AbortSignal) =>
    request<GithubConnect>(`/orgs/${orgId}/github/connect`, { signal }),

  /** `POST /orgs`. */
  create: (body: OrgCreate) =>
    request<Org>("/orgs", { method: "POST", body }),

  /** `PATCH /orgs/{org_id}`. */
  update: (orgId: ObjectId, body: OrgUpdate) =>
    request<Org>(`/orgs/${orgId}`, { method: "PATCH", body }),

  /** `DELETE /orgs/{org_id}`. */
  remove: (orgId: ObjectId) =>
    request<void>(`/orgs/${orgId}`, { method: "DELETE" }),

  /** `POST /orgs/{org_id}/invites`. */
  createInvite: (orgId: ObjectId, body: OrgInviteCreate) =>
    request<OrgInvite>(`/orgs/${orgId}/invites`, { method: "POST", body }),

  /** `POST /orgs/{org_id}/invites/{token}/accept`. */
  acceptInvite: (orgId: ObjectId, token: string) =>
    request<Org>(`/orgs/${orgId}/invites/${token}/accept`, { method: "POST" }),

  /**
   * `POST /orgs/invites/{token}/accept` — accept by token alone. The org is
   * resolved server-side from the token; used by the join-org page, which has
   * only the token from the invite link.
   */
  acceptInviteByToken: (token: string) =>
    request<Org>(`/orgs/invites/${token}/accept`, { method: "POST" }),
}

// ─── query hooks ─────────────────────────────────────────────────────────────

export function useMyOrgs(
  options?: Partial<UseQueryOptions<Org[]>>,
) {
  return useQuery({
    queryKey: queryKeys.orgs.mine(),
    queryFn: ({ signal }) => orgsApi.listMine(signal),
    ...options,
  })
}

export function useOrg(
  orgId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<Org>>,
) {
  return useQuery({
    queryKey: queryKeys.orgs.detail(orgId ?? ""),
    queryFn: ({ signal }) => orgsApi.get(orgId as ObjectId, signal),
    enabled: !!orgId,
    ...options,
  })
}

export function useOrgMembers(
  orgId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<OrgMemberRead[]>>,
) {
  return useQuery({
    queryKey: queryKeys.orgs.members(orgId ?? ""),
    queryFn: ({ signal }) => orgsApi.listMembers(orgId as ObjectId, signal),
    enabled: !!orgId,
    ...options,
  })
}

export function useOrgRepos(
  orgId: ObjectId | undefined,
  options?: Partial<UseQueryOptions<Repo[]>>,
) {
  return useQuery({
    queryKey: queryKeys.orgs.repos(orgId ?? ""),
    queryFn: ({ signal }) => orgsApi.listRepos(orgId as ObjectId, signal),
    enabled: !!orgId,
    ...options,
  })
}

// ─── mutation hooks ──────────────────────────────────────────────────────────

export function useCreateOrg(
  options?: UseMutationOptions<Org, Error, OrgCreate>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: OrgCreate) => orgsApi.create(body),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.orgs.lists() })
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useUpdateOrg(
  orgId: ObjectId,
  options?: UseMutationOptions<Org, Error, OrgUpdate>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: OrgUpdate) => orgsApi.update(orgId, body),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.setQueryData(queryKeys.orgs.detail(orgId), data)
      qc.invalidateQueries({ queryKey: queryKeys.orgs.lists() })
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

export function useDeleteOrg(
  options?: UseMutationOptions<void, Error, ObjectId>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (orgId: ObjectId) => orgsApi.remove(orgId),
    ...options,
    onSuccess: (data, orgId, ...rest) => {
      qc.removeQueries({ queryKey: queryKeys.orgs.detail(orgId) })
      qc.invalidateQueries({ queryKey: queryKeys.orgs.lists() })
      options?.onSuccess?.(data, orgId, ...rest)
    },
  })
}

export function useCreateOrgInvite(
  orgId: ObjectId,
  options?: UseMutationOptions<OrgInvite, Error, OrgInviteCreate>,
) {
  return useMutation({
    mutationFn: (body: OrgInviteCreate) => orgsApi.createInvite(orgId, body),
    ...options,
  })
}

export function useAcceptOrgInvite(
  options?: UseMutationOptions<Org, Error, { orgId: ObjectId; token: string }>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ orgId, token }: { orgId: ObjectId; token: string }) =>
      orgsApi.acceptInvite(orgId, token),
    ...options,
    onSuccess: (data, vars, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.orgs.lists() })
      options?.onSuccess?.(data, vars, ...rest)
    },
  })
}

/**
 * Accept an org invite by its token alone (the join-org page has only the token
 * from the invite link). Invalidates the org lists so the freshly-joined org
 * shows up on the next route resolve.
 */
export function useAcceptOrgInviteByToken(
  options?: UseMutationOptions<Org, Error, string>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => orgsApi.acceptInviteByToken(token),
    ...options,
    onSuccess: (data, token, ...rest) => {
      qc.invalidateQueries({ queryKey: queryKeys.orgs.lists() })
      options?.onSuccess?.(data, token, ...rest)
    },
  })
}

/**
 * Begin GitHub App installation for an org. Returns the install URL; the caller
 * decides when to redirect the browser to it (admin-only server-side).
 */
export function useConnectGithub(
  options?: UseMutationOptions<GithubConnect, Error, ObjectId>,
) {
  return useMutation({
    mutationFn: (orgId: ObjectId) => orgsApi.githubConnect(orgId),
    ...options,
  })
}
