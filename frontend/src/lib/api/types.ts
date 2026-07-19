/**
 * TypeScript mirrors of the backend Pydantic response/request schemas.
 *
 * These track the `schemas.py` modules under `app/`. Server-side
 * `PydanticObjectId` and
 * `datetime` both serialize to strings over JSON, so they're modelled as
 * branded string aliases below. Keep this file in sync when the schemas change.
 */

/** A MongoDB ObjectId, serialized as a 24-char hex string. */
export type ObjectId = string
/** An ISO-8601 datetime string. */
export type ISODateString = string

// ─── users (fastapi-users; GET/PATCH/DELETE /users/me) ───────────────────────

/** The authenticated user (`UserDocumentRead`). Own account — more fields than
 * the {@link UserPublic} view other members see. */
export interface User {
  id: ObjectId
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  sub: string | null
  name: string | null
  role: string | null
  githubUsername: string | null
  createdAt: ISODateString
}

/** Partial update for `PATCH /users/me` — every field optional. */
export interface UserUpdate {
  password?: string | null
  email?: string | null
  is_active?: boolean | null
  is_superuser?: boolean | null
  is_verified?: boolean | null
  sub?: string | null
  name?: string | null
  role?: string | null
  githubUsername?: string | null
}

// ─── orgs (app/orgs/schemas.py, app/orgs/models.py) ──────────────────────────

export type OrgRole = "admin" | "member"

/** Embedded member reference on an Org document. */
export interface OrgMember {
  userId: ObjectId
  role: OrgRole
  joinedAt: ISODateString
}

export interface Org {
  id: ObjectId
  name: string
  slug: string
  githubInstallationId: number | null
  bbAssistantId: string
  members: OrgMember[]
  createdAt: ISODateString
}

/** Non-sensitive user fields surfaced to other org members. */
export interface UserPublic {
  id: ObjectId
  email: string
  name: string | null
  role: string | null
  githubUsername: string | null
  createdAt: ISODateString
}

export interface OrgMemberRead {
  user: UserPublic
  role: OrgRole
  joinedAt: ISODateString
}

export interface Repo {
  id: ObjectId
  orgId: ObjectId
  githubRepoId: number
  owner: string
  name: string
  defaultBranch: string
  createdAt: ISODateString
}

export interface OrgInvite {
  id: ObjectId
  orgId: ObjectId
  email: string
  token: string
  expiresAt: ISODateString
  acceptedAt: ISODateString | null
}

export interface OrgCreate {
  name: string
}

export interface OrgUpdate {
  name?: string | null
  githubInstallationId?: number | null
}

export interface OrgInviteCreate {
  email: string
}

/** Response of `GET /orgs/{org_id}/github/connect`. */
export interface GithubConnect {
  installUrl: string
}

// ─── api keys (app/api_auth/schemas.py) ──────────────────────────────────────

export interface ApiKey {
  id: ObjectId
  label: string
  orgId: ObjectId
  lastUsedAt: ISODateString | null
  createdAt: ISODateString
}

/** Returned once on creation — `key` is the raw secret, never retrievable again. */
export interface ApiKeyCreated {
  id: ObjectId
  label: string
  orgId: ObjectId
  key: string
  createdAt: ISODateString
}

export interface ApiKeyCreate {
  label: string
  /** Required only when the caller belongs to more than one org. */
  orgId?: ObjectId | null
}

// ─── graph (app/graph/schemas.py) ────────────────────────────────────────────

export type NodeType = "decision" | "file" | "pr" | "engineer" | "feature"
export type EdgeKind =
  | "governs"
  | "introduced"
  | "made"
  | "belongs_to"
  | "superseded_by"
export type StalenessStatus = "fresh" | "stale" | "gap"
export type Confidence = "verified" | "unverified"

export interface GraphNodeMeta {
  prNumber?: number | null
  author?: string | null
  date?: ISODateString | null
  stalenessStatus?: StalenessStatus | null
  confidence?: Confidence | null
  path?: string | null
  decisionCount?: number | null
}

export interface GraphNode {
  id: string
  type: NodeType
  label: string
  /** Render size — degree-derived. */
  val: number
  meta: GraphNodeMeta
}

export interface GraphLink {
  source: string
  target: string
  kind: EdgeKind
  /** Populated on `governs` edges only. */
  symbols?: string[] | null
}

/** The {nodes, links} shape react-force-graph consumes. */
export interface GraphPayload {
  nodes: GraphNode[]
  links: GraphLink[]
}

/** Optional filters for `GET /orgs/{org_id}/graph`. */
export interface GraphFilters {
  /** Repos to include (full `owner/name`). Omitted/empty = every repo. */
  repos?: string[]
  feature?: string
  /** Node types to include, e.g. `["decision", "feature"]`. */
  types?: NodeType[]
}

/** A decision reachable from a non-decision node — a clickable hop target.
 * `id` is the graph node id (`dec:<oid>`). */
export interface RelatedDecision {
  id: string
  label: string
  prNumber?: number | null
  author?: string | null
  date: ISODateString
  stalenessStatus?: StalenessStatus | null
}

/** Full detail for one node (`GET /orgs/{org_id}/graph/nodes/{node_id}`).
 * Decision nodes carry the complete snapshot + provenance; every other node
 * type carries the decisions it connects to (`relatedDecisions`). */
export interface NodeDetail {
  id: string
  type: NodeType
  label: string
  // decision nodes
  contentSnapshot?: string | null
  prNumber?: number | null
  prUrl?: string | null
  author?: string | null
  date?: ISODateString | null
  feature?: string | null
  files?: string[] | null
  symbols?: string[] | null
  stalenessStatus?: StalenessStatus | null
  confidence?: Confidence | null
  supersededBy?: string | null
  // non-decision nodes (file, pr, engineer, feature)
  relatedDecisions?: RelatedDecision[] | null
}

// ─── documents (app/file_upload/schemas.py) ──────────────────────────────────

export type DocumentKind = "upload" | "decision_digest"
export type DocumentStatus = "pending" | "processing" | "indexed" | "error"
/**
 * Background enrichment + gap-detection phase, separate from `status` (which
 * only tracks RAG indexing — a doc reads "indexed" while this is still
 * "enriching"). "none" for docs that aren't repo-scoped and so never enrich.
 */
export type EnrichmentStatus = "none" | "enriching" | "done" | "failed"

export interface Document {
  id: ObjectId
  orgId: ObjectId
  repoId: ObjectId | null
  bbDocumentId: string
  filename: string
  kind: DocumentKind
  status: DocumentStatus
  enrichmentStatus: EnrichmentStatus
  /** Enrichment outcome, meaningful once `enrichmentStatus === "done"`. */
  decisionsWritten: number
  gapsOpened: number
  createdAt: ISODateString
  /** Present once `status === "indexed"`. */
  chunkCount: number | null
  totalTokens: number | null
  /** Present once `status === "error"`. */
  error: string | null
  recommendation: string | null
}

// ─── gap chats (app/gap_chat/schemas.py, app/gap_chat/models.py) ─────────────

export type GapChatStatus = "open" | "verified" | "superseded" | "dismissed"
export type GapResolution = "verified" | "superseded"

export interface GapMessage {
  role: "assistant" | "user"
  text: string
  createdAt: ISODateString
}

export interface GapChat {
  id: ObjectId
  orgId: ObjectId
  repoId: ObjectId
  bbMemoryId: string
  memoryContent: string
  changedFiles: string[]
  prNumber: number | null
  triggerStatus: "stale" | "gap"
  messages: GapMessage[]
  status: GapChatStatus
  supersededByMemoryId: string | null
  resolvedAt: ISODateString | null
  createdAt: ISODateString
}

export interface AnswerRequest {
  answer: string
}

export interface AnswerResult {
  chat: GapChat
  resolution: GapResolution
  supersededByMemoryId: string | null
  /** Populated only for voice answers. */
  transcript: string | null
}
