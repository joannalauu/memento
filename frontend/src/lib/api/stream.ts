/**
 * SSE-over-POST transport — the streaming sibling of http.ts's `request`.
 *
 * `EventSource` can't POST a body, so this does a raw `fetch` (same
 * `credentials: "include"` cookie auth) and hand-parses the response stream:
 * frames are `data: <json>\n\n` blocks, and a network chunk may end mid-frame,
 * so bytes accumulate in a buffer that's split on the frame delimiter.
 *
 * Non-2xx responses (the server rejects before streaming) throw the same
 * {@link ApiError} as the JSON client. Aborting the signal rejects with an
 * `AbortError` — callers decide whether that's an error or a cancel.
 */
import { API_BASE_URL } from "./config"
import { throwApiError } from "./http"

export interface StreamSSEOptions {
  /** JSON-serialized request body. */
  body: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Called once per parsed `data:` frame, in arrival order. */
  onData: (data: unknown) => void
}

/**
 * POST JSON to `path`, consume the SSE response, and resolve when the server
 * closes the stream. Whether the stream ended *successfully* is the caller's
 * call (e.g. did a terminal frame arrive?) — the transport only parses.
 */
export async function streamSSE(
  path: string,
  { body, headers = {}, signal, onData }: StreamSSEOptions,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    credentials: "include",
    signal,
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  })
  if (!response.ok) await throwApiError(response)
  if (!response.body) throw new Error("Streaming responses are not supported")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  for (;;) {
    const { done, value } = await reader.read() // rejects AbortError on abort
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let idx: number
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue // comments / other SSE fields
        const payload = line.slice(5).trimStart()
        try {
          onData(JSON.parse(payload))
        } catch {
          // A malformed frame is dropped rather than killing the stream.
        }
      }
    }
  }
  // A leftover partial block means the connection died mid-frame (the server
  // always terminates frames with \n\n) — nothing usable to deliver.
}
