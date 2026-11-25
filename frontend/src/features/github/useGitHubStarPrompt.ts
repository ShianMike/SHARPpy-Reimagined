import { useEffect, useRef, useState } from "react";
import { isGitHubStarEligible } from "../../stores/githubStarPreferences";
import { useUiStore } from "../../stores/uiStore";

export const REQUIRED_INTERACTIONS = 4;
export const INTERACTION_COOLDOWN_MS = 1_000;
export const INACTIVITY_DELAY_MS = 30_000;

const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "Meta"]);

export function useGitHubStarPrompt(blocked: boolean): boolean {
  const githubStar = useUiStore((state) => state.githubStar);
  const eligible = isGitHubStarEligible(githubStar);
  const [ready, setReady] = useState(false);
  const interactionsRef = useRef(0);
  const lastCountedAtRef = useRef(-Infinity);

  useEffect(() => {
    if (!eligible || blocked) return;
    let timer: number | null = null;
    let pendingVisible = false;

    const clearTimer = (): void => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const scheduleInactivity = (): void => {
      if (interactionsRef.current < REQUIRED_INTERACTIONS) return;
      clearTimer();
      timer = window.setTimeout(() => {
        timer = null;
        if (document.visibilityState === "visible") {
          setReady(true);
        } else {
          pendingVisible = true;
        }
      }, INACTIVITY_DELAY_MS);
    };

    const recordActivity = (countInteraction: boolean): void => {
      pendingVisible = false;
      if (countInteraction) {
        const now = Date.now();
        if (now - lastCountedAtRef.current >= INTERACTION_COOLDOWN_MS) {
          interactionsRef.current += 1;
          lastCountedAtRef.current = now;
        }
      }
      scheduleInactivity();
    };

    const onPointerDown = (): void => { recordActivity(true); };
    const onKeyDown = (event: KeyboardEvent): void => {
      recordActivity(!MODIFIER_KEYS.has(event.key));
    };
    const onActivity = (): void => { recordActivity(false); };
    const onVisibility = (): void => {
      if (document.visibilityState === "visible" && pendingVisible) {
        pendingVisible = false;
        setReady(true);
      }
    };

    scheduleInactivity();
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("wheel", onActivity, true);
    window.addEventListener("input", onActivity, true);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      clearTimer();
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("wheel", onActivity, true);
      window.removeEventListener("input", onActivity, true);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [eligible, blocked]);

  return eligible && !blocked && ready;
}
