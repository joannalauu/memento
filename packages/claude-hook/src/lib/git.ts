import { execFileSync } from "node:child_process";

export interface GitInfo {
  remote: string | null;
  branch: string | null;
}

/** Run a git subcommand in `cwd`, returning trimmed stdout or null on any failure. */
function git(cwd: string, args: string[]): string | null {
  try {
    const out = execFileSync("git", args, {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 2_000,
    });
    const trimmed = out.trim();
    return trimmed === "" ? null : trimmed;
  } catch {
    return null;
  }
}

/**
 * Resolve the origin remote URL and current branch for `cwd`. Never throws;
 * either field is null when the directory is not a git repo or the value is
 * unavailable (e.g. detached HEAD, no remote configured).
 */
export function resolveGit(cwd: string): GitInfo {
  const remote = git(cwd, ["remote", "get-url", "origin"]);
  let branch = git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]);
  if (branch === "HEAD") branch = null; // detached
  return { remote, branch };
}
