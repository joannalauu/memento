import { existsSync, rmSync } from "node:fs";

import { CONFIG_PATH, HOOK_EVENT } from "../constants.js";
import { projectSettingsPath, readSettings, removeHook, writeSettings } from "../lib/settings.js";

interface UninstallFlags {
  purge: boolean;
  cwd: string;
}

function parseFlags(argv: string[]): UninstallFlags {
  const flags: UninstallFlags = { purge: false, cwd: process.cwd() };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--purge") flags.purge = true;
    else if (arg === "--cwd") flags.cwd = argv[++i] ?? flags.cwd;
  }
  return flags;
}

/**
 * `memento-hook uninstall [--purge]`
 *
 * Removes our SessionEnd hook entry from the project settings, leaving all other
 * settings intact. With --purge, also deletes the home config (API key).
 */
export async function uninstall(argv: string[]): Promise<number> {
  const flags = parseFlags(argv);
  const settingsPath = projectSettingsPath(flags.cwd);

  let removed = false;
  if (existsSync(settingsPath)) {
    try {
      const settings = readSettings(settingsPath);
      removed = removeHook(settings);
      if (removed) writeSettings(settingsPath, settings);
    } catch (err) {
      process.stderr.write(`error: could not update ${settingsPath}: ${(err as Error).message}\n`);
      return 1;
    }
  }

  process.stdout.write(
    `${removed ? "Removed" : "No"} Memento ${HOOK_EVENT} hook ${removed ? "from" : "found in"} ${settingsPath}.\n`,
  );

  if (flags.purge && existsSync(CONFIG_PATH)) {
    rmSync(CONFIG_PATH, { force: true });
    process.stdout.write(`Purged config -> ${CONFIG_PATH}\n`);
  }
  return 0;
}
