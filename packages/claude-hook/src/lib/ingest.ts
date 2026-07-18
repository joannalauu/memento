import { gzipSync } from "node:zlib";

import { FETCH_TIMEOUT_MS, HEADERS, INGEST_PATH } from "../constants.js";

export interface IngestInput {
  baseUrl: string;
  apiKey: string;
  sessionId: string;
  branch: string | null;
  remote: string | null;
  tokenEstimate: number | null;
  hookVersion: string;
  /** Redacted raw JSONL transcript. */
  transcript: string;
}

export interface IngestResult {
  ok: boolean;
  status: number;
}

/**
 * Gzip the redacted transcript and POST it to the ingest endpoint. Metadata
 * travels in headers; the body is the raw gzipped JSONL. Bounded by an
 * AbortController so a hung server can never stall the hook. Never throws.
 */
export async function ingest(input: IngestInput): Promise<IngestResult> {
  const body = gzipSync(Buffer.from(input.transcript, "utf8"));

  const headers: Record<string, string> = {
    Authorization: `Bearer ${input.apiKey}`,
    "Content-Type": "application/x-ndjson",
    "Content-Encoding": "gzip",
    [HEADERS.sessionId]: input.sessionId,
    [HEADERS.hookVersion]: input.hookVersion,
  };
  if (input.branch) headers[HEADERS.branch] = input.branch;
  if (input.remote) headers[HEADERS.remote] = input.remote;
  if (input.tokenEstimate != null) {
    headers[HEADERS.tokenEstimate] = String(input.tokenEstimate);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(input.baseUrl + INGEST_PATH, {
      method: "POST",
      headers,
      body,
      signal: controller.signal,
    });
    return { ok: res.ok || res.status === 409, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  } finally {
    clearTimeout(timer);
  }
}
