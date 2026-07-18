import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { RUN_BUDGET_MS } from "./constants.js";
import { loadConfig } from "./lib/config.js";
import { resolveGit } from "./lib/git.js";
import { ingest } from "./lib/ingest.js";
import { redact } from "./lib/redact.js";

interface HookInput {
  session_id?: string;
  transcript_path?: string;
  cwd?: string;
}

/** Read all of stdin as UTF-8. Resolves to "" if the stream never provides data. */
function readStdin(): Promise<string> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (c: Buffer) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", () => resolve(""));
  });
}

/** Read our own package version for the X-Hook-Version header. */
function hookVersion(): string {
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
    return typeof pkg.version === "string" ? pkg.version : "0.0.0";
  } catch {
    return "0.0.0";
  }
}

/** Very rough token estimate (~4 chars/token) — advisory only. */
function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/**
 * The SessionEnd hook body. Captures the transcript, redacts, gzips, and POSTs.
 * Every failure mode resolves quietly — callers must always exit 0.
 */
async function main(): Promise<void> {
  const config = loadConfig();
  if (!config) return; // not configured for this engineer → nothing to do

  const stdin = await readStdin();
  if (!stdin.trim()) return;

  let input: HookInput;
  try {
    input = JSON.parse(stdin) as HookInput;
  } catch {
    return; // malformed stdin → bail
  }

  const sessionId = input.session_id;
  const transcriptPath = input.transcript_path;
  if (!sessionId || !transcriptPath) return;

  let raw: string;
  try {
    raw = readFileSync(transcriptPath, "utf8");
  } catch {
    return; // transcript unreadable → nothing to send
  }
  if (!raw.trim()) return;

  const cwd = input.cwd || process.cwd();
  const { remote, branch } = resolveGit(cwd);
  const transcript = redact(raw, config.apiKey);

  await ingest({
    baseUrl: config.baseUrl,
    apiKey: config.apiKey,
    sessionId,
    branch,
    remote,
    tokenEstimate: estimateTokens(transcript),
    hookVersion: hookVersion(),
    transcript,
  });
}

/**
 * Entry point. Guarantees the process exits 0 promptly no matter what — a hook
 * must never block or delay a Claude Code session.
 */
export async function run(): Promise<number> {
  const budget = setTimeout(() => process.exit(0), RUN_BUDGET_MS);
  budget.unref();
  try {
    await main();
  } catch {
    /* fail silent */
  } finally {
    clearTimeout(budget);
  }
  return 0;
}
