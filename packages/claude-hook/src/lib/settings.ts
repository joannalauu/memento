import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { HOOK_COMMAND, HOOK_EVENT, HOOK_TIMEOUT_SECONDS, PACKAGE_NAME } from "../constants.js";

/** A single command entry inside a hook matcher group. */
interface HookCommand {
  type: "command";
  command: string;
  timeout?: number;
  [key: string]: unknown;
}

/** A matcher group: an optional matcher plus its list of command hooks. */
interface HookGroup {
  matcher?: string;
  hooks?: HookCommand[];
  [key: string]: unknown;
}

/** The subset of settings.json we touch. Unknown keys are preserved verbatim. */
interface Settings {
  hooks?: Record<string, HookGroup[]>;
  [key: string]: unknown;
}

/** Resolve the project settings.json path for a given directory. */
export function projectSettingsPath(cwd: string): string {
  return join(cwd, ".claude", "settings.json");
}

/** Read settings.json, returning `{}` when the file is absent. Throws on malformed JSON. */
export function readSettings(path: string): Settings {
  if (!existsSync(path)) return {};
  const raw = readFileSync(path, "utf8").trim();
  if (raw === "") return {};
  const parsed = JSON.parse(raw);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${path} is not a JSON object`);
  }
  return parsed as Settings;
}

/** Write settings.json with 2-space indent and a trailing newline, creating dirs as needed. */
export function writeSettings(path: string, settings: Settings): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(settings, null, 2) + "\n", "utf8");
}

/** True if a command entry belongs to us (identified by the package name). */
function isOurCommand(cmd: HookCommand): boolean {
  return typeof cmd.command === "string" && cmd.command.includes(PACKAGE_NAME);
}

/**
 * Add our SessionEnd hook to `settings`, preserving every other key, hook event,
 * and third-party entry. Idempotent: a re-install refreshes our entry in place
 * rather than appending a duplicate. Returns whether anything changed.
 */
export function addHook(settings: Settings): boolean {
  const hooks = (settings.hooks ??= {});
  const groups = (hooks[HOOK_EVENT] ??= []);

  const ours: HookCommand = {
    type: "command",
    command: HOOK_COMMAND,
    timeout: HOOK_TIMEOUT_SECONDS,
  };

  // Refresh an existing entry if we already own one anywhere in the event.
  for (const group of groups) {
    const list = group.hooks;
    if (!Array.isArray(list)) continue;
    const idx = list.findIndex(isOurCommand);
    if (idx !== -1) {
      const before = JSON.stringify(list[idx]);
      list[idx] = { ...list[idx], ...ours };
      return JSON.stringify(list[idx]) !== before;
    }
  }

  groups.push({ hooks: [ours] });
  return true;
}

/**
 * Remove every hook entry we own from `settings`, pruning now-empty groups and
 * the event/`hooks` container when they end up empty. Returns whether anything changed.
 */
export function removeHook(settings: Settings): boolean {
  const hooks = settings.hooks;
  const groups = hooks?.[HOOK_EVENT];
  if (!hooks || !Array.isArray(groups)) return false;

  let changed = false;
  for (const group of groups) {
    if (!Array.isArray(group.hooks)) continue;
    const kept = group.hooks.filter((cmd) => !isOurCommand(cmd));
    if (kept.length !== group.hooks.length) {
      group.hooks = kept;
      changed = true;
    }
  }

  // Drop groups that we emptied and that carry no other config.
  const remaining = groups.filter(
    (g) => !(Array.isArray(g.hooks) && g.hooks.length === 0 && g.matcher === undefined),
  );
  if (remaining.length !== groups.length) changed = true;

  if (remaining.length === 0) {
    delete hooks[HOOK_EVENT];
  } else {
    hooks[HOOK_EVENT] = remaining;
  }
  if (Object.keys(hooks).length === 0) delete settings.hooks;

  return changed;
}

export type { Settings };
