import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

import {
  CONFIG_DIR,
  CONFIG_PATH,
  DEFAULT_BASE_URL,
  ENV_API_KEY,
  ENV_BASE_URL,
} from "../constants.js";

export interface HookConfig {
  apiKey: string;
  baseUrl: string;
}

/** Persist the API key + base URL to the home config file with 0600 perms. */
export function writeConfig(config: HookConfig): void {
  mkdirSync(CONFIG_DIR, { recursive: true });
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + "\n", "utf8");
  // Best-effort tightening; harmless no-op where unsupported (e.g. Windows).
  try {
    chmodSync(CONFIG_PATH, 0o600);
  } catch {
    /* ignore */
  }
}

/** Read the on-disk config, or null when absent/unreadable. */
function readConfigFile(): Partial<HookConfig> | null {
  try {
    if (!existsSync(CONFIG_PATH)) return null;
    const parsed = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
    if (typeof parsed !== "object" || parsed === null) return null;
    return parsed as Partial<HookConfig>;
  } catch {
    return null;
  }
}

/**
 * Resolve the effective config for a run: env vars win over the file, and the
 * base URL falls back to the built-in default. Returns null only when no API
 * key can be found anywhere (the run should then no-op silently).
 */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): HookConfig | null {
  const file = readConfigFile() ?? {};
  const apiKey = env[ENV_API_KEY]?.trim() || file.apiKey?.trim();
  if (!apiKey) return null;
  const baseUrl = (env[ENV_BASE_URL]?.trim() || file.baseUrl?.trim() || DEFAULT_BASE_URL).replace(
    /\/+$/,
    "",
  );
  return { apiKey, baseUrl };
}
