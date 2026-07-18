/**
 * Centralized React Query key factory.
 *
 * Every key is derived from these builders so that invalidation stays precise
 * and consistent. Keys are hierarchical: invalidating `orgs.all` also matches
 * every nested org query (list, detail, members, repos, …).
 */
import type { GraphFilters, GapChatStatus, ObjectId } from "./types"

export const queryKeys = {
  users: {
    all: ["users"] as const,
    /** The authenticated user (`GET /users/me`). */
    me: () => [...queryKeys.users.all, "me"] as const,
  },

  orgs: {
    all: ["orgs"] as const,
    lists: () => [...queryKeys.orgs.all, "list"] as const,
    /** The authenticated user's orgs (`GET /orgs/me`). */
    mine: () => [...queryKeys.orgs.lists(), "me"] as const,
    details: () => [...queryKeys.orgs.all, "detail"] as const,
    detail: (orgId: ObjectId) => [...queryKeys.orgs.details(), orgId] as const,
    members: (orgId: ObjectId) =>
      [...queryKeys.orgs.detail(orgId), "members"] as const,
    repos: (orgId: ObjectId) =>
      [...queryKeys.orgs.detail(orgId), "repos"] as const,
  },

  apiKeys: {
    all: ["api-keys"] as const,
    lists: () => [...queryKeys.apiKeys.all, "list"] as const,
    details: () => [...queryKeys.apiKeys.all, "detail"] as const,
    detail: (keyId: ObjectId) =>
      [...queryKeys.apiKeys.details(), keyId] as const,
  },

  graph: {
    all: ["graph"] as const,
    detail: (orgId: ObjectId, filters?: GraphFilters) =>
      [...queryKeys.graph.all, orgId, filters ?? {}] as const,
    /** Detail for one clicked node (`GET /orgs/{org_id}/graph/nodes/{node_id}`). */
    node: (orgId: ObjectId, nodeId: string) =>
      [...queryKeys.graph.all, orgId, "node", nodeId] as const,
  },

  documents: {
    all: ["documents"] as const,
    lists: () => [...queryKeys.documents.all, "list"] as const,
    list: (orgId: ObjectId) => [...queryKeys.documents.lists(), orgId] as const,
    details: () => [...queryKeys.documents.all, "detail"] as const,
    detail: (orgId: ObjectId, docId: ObjectId) =>
      [...queryKeys.documents.details(), orgId, docId] as const,
  },

  gapChats: {
    all: ["gap-chats"] as const,
    lists: () => [...queryKeys.gapChats.all, "list"] as const,
    list: (orgId: ObjectId, status?: GapChatStatus) =>
      [...queryKeys.gapChats.lists(), orgId, status ?? "all"] as const,
    details: () => [...queryKeys.gapChats.all, "detail"] as const,
    detail: (orgId: ObjectId, chatId: ObjectId) =>
      [...queryKeys.gapChats.details(), orgId, chatId] as const,
  },
} as const
