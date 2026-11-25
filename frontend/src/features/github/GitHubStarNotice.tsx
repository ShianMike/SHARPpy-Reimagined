import { Check, LoaderCircle, Star, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { recordLog } from "../../stores/logStore";
import { GITHUB_STAR_BROWSER_SNOOZE_MS, GITHUB_STAR_NOT_NOW_MS } from "../../stores/githubStarPreferences";
import { useUiStore } from "../../stores/uiStore";
import { useUpdateStore } from "../../stores/updateStore";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { GITHUB_REPO_URL, starGitHubRepo } from "./githubStar";
import styles from "./GitHubStarNotice.module.css";
import { useGitHubStarPrompt } from "./useGitHubStarPrompt";

type StarNoticePhase = "idle" | "starring" | "starred" | "error";

const STARRED_THANK_YOU_MS = 3_000;

export function GitHubStarNotice(): ReactNode {
  const whatsNewOpen = useUiStore((state) => state.whatsNewOpen);
  const reviewOpen = useUpdateStore((state) => state.installReview !== null);
  const promptVisible = useGitHubStarPrompt(whatsNewOpen || reviewOpen);
  const [phase, setPhase] = useState<StarNoticePhase>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sessionClosed, setSessionClosed] = useState(false);

  useEffect(() => {
    if (phase !== "starred" || sessionClosed) return;
    const timer = window.setTimeout(() => { setSessionClosed(true); }, STARRED_THANK_YOU_MS);
    return () => { window.clearTimeout(timer); };
  }, [phase, sessionClosed]);

  if (sessionClosed) return null;
  if (phase === "starred") {
    return (
      <Surface elevation="floating" className={styles.notice} data-motion="enter" data-state="starred" role="status" data-testid="github-star-notice">
        <div className={styles.header}>
          <span className={styles.icon} aria-hidden="true"><Check /></span>
          <div className={styles.content}>
            <p className={styles.title}>Thanks for starring SHARPpy Reimagined!</p>
            <p className={styles.message}>Your support helps the project grow.</p>
          </div>
          <IconAction icon={X} size="sm" label="Close" onClick={() => { setSessionClosed(true); }} />
        </div>
      </Surface>
    );
  }
  if (!promptVisible) return null;

  const busy = phase === "starring";
  const handleNotNow = (): void => {
    useUiStore.getState().setGitHubStar({ status: "snoozed", snoozedUntil: Date.now() + GITHUB_STAR_NOT_NOW_MS });
    setSessionClosed(true);
  };
  const handleDismiss = (): void => {
    useUiStore.getState().setGitHubStar({ status: "dismissed", snoozedUntil: null });
    setSessionClosed(true);
  };
  const handleStar = async (): Promise<void> => {
    setPhase("starring");
    setErrorMessage(null);
    try {
      const outcome = await starGitHubRepo();
      if (outcome.status === "starred") {
        useUiStore.getState().setGitHubStar({ status: "starred", snoozedUntil: null });
        setPhase("starred");
      } else {
        useUiStore.getState().setGitHubStar({ status: "snoozed", snoozedUntil: Date.now() + GITHUB_STAR_BROWSER_SNOOZE_MS });
        setSessionClosed(true);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      recordLog("warning", "github-star", "Couldn’t star the GitHub repository", message);
      setPhase("error");
      setErrorMessage("Couldn’t star via GitHub right now.");
    }
  };

  return (
    <Surface elevation="floating" className={styles.notice} data-motion="enter" data-state={phase} role="status" data-testid="github-star-notice">
      <div className={styles.header}>
        <span className={styles.icon} aria-hidden="true"><Star /></span>
        <div className={styles.content}>
          <p className={styles.title}>Enjoying SHARPpy Reimagined?</p>
          <p className={styles.message}>Star the project on GitHub to support development.</p>
          {errorMessage === null ? null : <>
            <p className={styles.errorText}>{errorMessage}</p>
            <a className={styles.fallbackLink} href={GITHUB_REPO_URL} target="_blank" rel="noopener noreferrer">
              Open {GITHUB_REPO_URL.replace("https://", "")}
            </a>
          </>}
        </div>
        <IconAction icon={X} size="sm" label="Don’t ask again" disabled={busy} onClick={handleDismiss} />
      </div>
      <div className={styles.actions}>
        <Action disabled={busy} onClick={handleNotNow}>Not Now</Action>
        <Action tone="accent" disabled={busy} onClick={() => { void handleStar(); }}>
          {busy ? <LoaderCircle className="icon-sm" data-loading="true" aria-hidden="true" /> : <Star className="icon-sm" aria-hidden="true" />}
          {busy ? "Starring…" : "Star on GitHub"}
        </Action>
      </div>
    </Surface>
  );
}
