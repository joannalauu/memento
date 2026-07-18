import { homedir } from "node:os";
import { join } from "node:path";

/** npm package name — also the marker used to detect our own hook entry. */
export const PACKAGE_NAME = "@memento/hook";

/** Command written into settings.json. `-y` avoids the npx install prompt. */
export const HOOK_COMMAND = `npx -y ${PACKAGE_NAME} run`;

/** The Claude Code hook event we install into. */
export const HOOK_EVENT = "SessionEnd" as const;

/** Per-hook timeout (seconds) Claude Code allows before killing. */
export const HOOK_TIMEOUT_SECONDS = 15;

/** Default ingest base URL. Override at install time (--url) or via env. */
export const DEFAULT_BASE_URL = "https://memento-zxan.onrender.com";

/** Ingest path appended to the base URL. */
export const INGEST_PATH = "/ingest/agent-sessions";

/** Where the engineer's API key + base URL live (never committed). */
export const CONFIG_DIR = join(homedir(), ".claude", "memento-hook");
export const CONFIG_PATH = join(CONFIG_DIR, "config.json");

/** Env overrides, checked before the on-disk config. */
export const ENV_API_KEY = "MEMENTO_API_KEY";
export const ENV_BASE_URL = "MEMENTO_INGEST_URL";

/** Wall-clock budget for the whole `run` handler before we give up (ms). */
export const RUN_BUDGET_MS = 10_000;
/** AbortController budget for the network POST alone (ms). */
export const FETCH_TIMEOUT_MS = 5_000;

/** Wire headers — kept in one place so the backend author can mirror them. */
export const HEADERS = {
  sessionId: "X-Session-Id",
  branch: "X-Git-Branch",
  remote: "X-Git-Remote",
  tokenEstimate: "X-Token-Estimate",
  hookVersion: "X-Hook-Version",
} as const;
