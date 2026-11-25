import type { GitHubStarOutcome } from "../../lib/backend/tauriTransport";

export const GITHUB_REPO_URL = "https://github.com/ShianMike/SHARPpy-Reimagined";

export async function starGitHubRepo(): Promise<GitHubStarOutcome> {
  if ("__TAURI_INTERNALS__" in window) {
    const { starGitHubRepository } = await import("../../lib/backend/tauriTransport");
    return starGitHubRepository();
  }
  window.open(GITHUB_REPO_URL, "_blank", "noopener,noreferrer");
  return { status: "openedBrowser", url: GITHUB_REPO_URL };
}
