#!/usr/bin/env node
import { install } from "./commands/install.js";
import { uninstall } from "./commands/uninstall.js";
import { PACKAGE_NAME } from "./constants.js";
import { run } from "./run.js";

const HELP = `${PACKAGE_NAME} — Claude Code session-ingest hook

Usage:
  npx ${PACKAGE_NAME} install [--api-key <key>] [--url <baseUrl>]
      Write the SessionEnd hook into ./.claude/settings.json and save your
      API key to ~/.claude/memento-hook/config.json.

  npx ${PACKAGE_NAME} uninstall [--purge]
      Remove the hook from ./.claude/settings.json. --purge also deletes the
      saved API key.

  npx ${PACKAGE_NAME} run
      The hook itself — reads the hook payload on stdin. Not meant to be run
      by hand; Claude Code invokes it on SessionEnd.

Environment:
  MEMENTO_API_KEY      overrides the stored API key
  MEMENTO_INGEST_URL   overrides the stored ingest base URL
`;

async function dispatch(argv: string[]): Promise<number> {
  const [command, ...rest] = argv;
  switch (command) {
    case "install":
      return install(rest);
    case "uninstall":
      return uninstall(rest);
    case "run":
      return run();
    case "--version":
    case "-v":
      // Printed lazily to avoid a package.json read on the hot `run` path.
      process.stdout.write(`${PACKAGE_NAME}\n`);
      return 0;
    case undefined:
    case "help":
    case "--help":
    case "-h":
      process.stdout.write(HELP);
      return 0;
    default:
      process.stderr.write(`unknown command: ${command}\n\n${HELP}`);
      return 1;
  }
}

// `run` manages its own exit (fail-silent, always 0). Other commands set the code.
dispatch(process.argv.slice(2))
  .then((code) => {
    if (process.argv[2] !== "run") process.exitCode = code;
  })
  .catch(() => {
    // Never surface a stack trace from the hook path.
    if (process.argv[2] !== "run") process.exitCode = 1;
  });
