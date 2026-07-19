import type { Repo } from "@/lib/api"

/**
 * Link a repo-relative path to its source on GitHub (default branch), suitable
 * for opening in a new tab. Returns null when the repo isn't known so callers
 * can fall back to rendering the path as plain text.
 */
export function githubBlobUrl(
  repo: Pick<Repo, "owner" | "name" | "defaultBranch"> | null | undefined,
  file: string,
): string | null {
  if (!repo) return null
  const path = file.split("/").map(encodeURIComponent).join("/")
  return `https://github.com/${repo.owner}/${repo.name}/blob/${repo.defaultBranch}/${path}`
}
