import { CONFIG_PATH, DEFAULT_BASE_URL, ENV_API_KEY, HOOK_EVENT } from "../constants.js";
import { writeConfig } from "../lib/config.js";
import { addHook, projectSettingsPath, readSettings, writeSettings } from "../lib/settings.js";

interface InstallFlags {
  apiKey?: string;
  url?: string;
  cwd: string;
}

function parseFlags(argv: string[]): InstallFlags {
  const flags: InstallFlags = { cwd: process.cwd() };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--api-key" || arg === "--key") flags.apiKey = argv[++i];
    else if (arg === "--url") flags.url = argv[++i];
    else if (arg === "--cwd") flags.cwd = argv[++i] ?? flags.cwd;
  }
  return flags;
}

/**
 * `memento-hook install [--api-key <key>] [--url <baseUrl>]`
 *
 * Writes the SessionEnd hook into the project's .claude/settings.json and stores
 * the engineer's API key + base URL in the home config file (0600).
 */
export async function install(argv: string[]): Promise<number> {
  const flags = parseFlags(argv);
  const apiKey = (flags.apiKey ?? process.env[ENV_API_KEY])?.trim();
  const baseUrl = (flags.url ?? DEFAULT_BASE_URL).replace(/\/+$/, "");

  if (!apiKey) {
    process.stderr.write(
      `error: no API key provided.\n` +
        `  pass --api-key <key> or set ${ENV_API_KEY} before running install.\n`,
    );
    return 1;
  }

  writeConfig({ apiKey, baseUrl });

  const settingsPath = projectSettingsPath(flags.cwd);
  let settings;
  try {
    settings = readSettings(settingsPath);
  } catch (err) {
    process.stderr.write(`error: could not read ${settingsPath}: ${(err as Error).message}\n`);
    return 1;
  }
  const changed = addHook(settings);
  writeSettings(settingsPath, settings);

  process.stdout.write(
    `${changed ? "Installed" : "Already installed"} the Memento ${HOOK_EVENT} hook.\n` +
      `  hook   -> ${settingsPath}\n` +
      `  config -> ${CONFIG_PATH} (chmod 600; do not commit)\n` +
      `  ingest -> ${baseUrl}\n`,
  );
  return 0;
}
