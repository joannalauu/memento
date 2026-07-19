/**
 * Core HTTP client shared by every resource module.
 *
 * Responsibilities:
 *  - Prefix requests with the configured API base URL.
 *  - Send cookies (`credentials: "include"`) so the Auth0 session rides along.
 *  - Serialize JSON bodies (and pass FormData through untouched for uploads).
 *  - Normalize FastAPI error payloads into a typed {@link ApiError}.
 *  - Decode 204 / empty responses to `undefined`.
 */
import { API_BASE_URL } from "./config"

/** A FastAPI request-validation error item (`422` responses). */
interface ValidationErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
}

/** Thrown for any non-2xx response. Carries the HTTP status and parsed detail. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string | ValidationErrorItem[] | null

  constructor(
    status: number,
    detail: string | ValidationErrorItem[] | null,
    message: string,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }

  /** True for 401 — the caller should send the user back through login. */
  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

/**
 * Optional global hook invoked whenever a request 401s. Wire this in the app
 * root (e.g. to redirect to the Auth0 login) via {@link setUnauthorizedHandler}.
 */
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue | QueryValue[]>

function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(`${API_BASE_URL}${path}`)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === null || value === undefined) continue
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item !== null && item !== undefined) {
            url.searchParams.append(key, String(item))
          }
        }
      } else {
        url.searchParams.set(key, String(value))
      }
    }
  }
  return url.toString()
}

function messageFromDetail(
  status: number,
  detail: string | ValidationErrorItem[] | null,
): string {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((item) => item.msg).join("; ")
  }
  return `Request failed with status ${status}`
}

/**
 * Normalize a non-2xx {@link Response} into a thrown {@link ApiError}: parse
 * the FastAPI `detail`, fire the global 401 hook. Shared by {@link request}
 * and the streaming client (lib/api/stream.ts), so both surface identical
 * errors.
 */
export async function throwApiError(response: Response): Promise<never> {
  let detail: string | ValidationErrorItem[] | null = null
  try {
    const data = await response.json()
    detail = data?.detail ?? null
  } catch {
    detail = null
  }
  if (response.status === 401) onUnauthorized?.()
  throw new ApiError(
    response.status,
    detail,
    messageFromDetail(response.status, detail),
  )
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE"
  params?: QueryParams
  /** JSON-serialized unless it's already FormData / a string. */
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

/**
 * Perform an API request and return the decoded JSON body (or `undefined` for
 * empty/204 responses). Throws {@link ApiError} on any non-2xx status.
 */
export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", params, body, signal, headers = {} } = options

  const init: RequestInit = {
    method,
    credentials: "include",
    signal,
    headers: { ...headers },
  }

  if (body !== undefined && body !== null) {
    if (body instanceof FormData || typeof body === "string") {
      init.body = body
    } else {
      init.body = JSON.stringify(body)
      ;(init.headers as Record<string, string>)["Content-Type"] =
        "application/json"
    }
  }

  const response = await fetch(buildUrl(path, params), init)

  if (!response.ok) await throwApiError(response)

  // 204 No Content and other empty bodies decode to undefined.
  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T
  }
  const text = await response.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}
