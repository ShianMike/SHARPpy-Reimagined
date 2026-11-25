export const GITHUB_STAR_NOT_NOW_MS = 7 * 24 * 60 * 60 * 1000;
export const GITHUB_STAR_BROWSER_SNOOZE_MS = 14 * 24 * 60 * 60 * 1000;

export type GitHubStarStatus = "none" | "snoozed" | "dismissed" | "starred";

export interface GitHubStarPreference {
  status: GitHubStarStatus;
  snoozedUntil: number | null;
}

export const DEFAULT_GITHUB_STAR_PREFERENCE: GitHubStarPreference = {
  status: "none",
  snoozedUntil: null,
};

export function parseGitHubStarPreference(raw: unknown): GitHubStarPreference {
  if (raw === null || typeof raw !== "object") return DEFAULT_GITHUB_STAR_PREFERENCE;
  const value = raw as Record<string, unknown>;
  switch (value.status) {
    case "none":
    case "dismissed":
    case "starred":
      return { status: value.status, snoozedUntil: null };
    case "snoozed":
      return typeof value.snoozedUntil === "number"
        && Number.isSafeInteger(value.snoozedUntil)
        && value.snoozedUntil > 0
        ? { status: "snoozed", snoozedUntil: value.snoozedUntil }
        : DEFAULT_GITHUB_STAR_PREFERENCE;
    default:
      return DEFAULT_GITHUB_STAR_PREFERENCE;
  }
}

export function isGitHubStarEligible(pref: GitHubStarPreference, now = Date.now()): boolean {
  if (pref.status === "none") return true;
  if (pref.status === "snoozed") return pref.snoozedUntil === null || now >= pref.snoozedUntil;
  return false;
}
