// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  DEFAULT_GITHUB_STAR_PREFERENCE,
  GITHUB_STAR_BROWSER_SNOOZE_MS,
  GITHUB_STAR_NOT_NOW_MS,
} from "../../stores/githubStarPreferences";
import { useAppStore } from "../../stores/appStore";
import { useLogStore } from "../../stores/logStore";
import { useUiStore } from "../../stores/uiStore";
import { useUpdateStore } from "../../stores/updateStore";
import { GITHUB_REPO_URL } from "./githubStar";
import { GitHubStarNotice } from "./GitHubStarNotice";
import { INACTIVITY_DELAY_MS, INTERACTION_COOLDOWN_MS, REQUIRED_INTERACTIONS } from "./useGitHubStarPrompt";

function interact(count: number, eventType: "pointerdown" | "keydown" = "pointerdown"): void {
  for (let i = 0; i < count; i++) {
    act(() => {
      if (eventType === "pointerdown") {
        fireEvent.pointerDown(window);
      } else {
        fireEvent.keyDown(window, { key: "a" });
      }
      if (i + 1 < count) vi.advanceTimersByTime(INTERACTION_COOLDOWN_MS);
    });
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-24T12:00:00Z"));
  useAppStore.setState({ ...useAppStore.getInitialState(), activeWorkspace: "map" }, true);
  useUiStore.setState({ whatsNewOpen: false, githubStar: DEFAULT_GITHUB_STAR_PREFERENCE });
  useUpdateStore.setState(useUpdateStore.getInitialState(), true);
  useLogStore.getState().clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useAppStore.setState(useAppStore.getInitialState(), true);
});

it("waits for enough cooled-down interactions plus 30s inactivity and resets on activity", () => {
  const invoke = vi.fn();
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  render(<GitHubStarNotice />);

  // Modifier-only keys and rapid uncooled clicks should not reach the threshold.
  act(() => {
    fireEvent.keyDown(window, { key: "Shift" });
    fireEvent.pointerDown(window);
    fireEvent.pointerDown(window);
    fireEvent.pointerDown(window);
    vi.advanceTimersByTime(INTERACTION_COOLDOWN_MS);
  });
  interact(REQUIRED_INTERACTIONS - 2);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS + 5_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();

  // Reach the interaction threshold, then interrupt the 30s idle period halfway.
  interact(1);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS - 1_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  act(() => { fireEvent.wheel(window); });
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS - 1_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();

  act(() => { vi.advanceTimersByTime(1_000); });
  expect(screen.getByTestId("github-star-notice")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Star on GitHub" })).toBeTruthy();
  // Never stars without explicit user consent.
  expect(invoke).not.toHaveBeenCalled();
});

it("waits until the window is visible again if inactivity expires while hidden", () => {
  let visibilityState: DocumentVisibilityState = "hidden";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibilityState);
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS, "keydown");
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();

  visibilityState = "visible";
  act(() => { fireEvent(document, new Event("visibilitychange")); });
  expect(screen.getByTestId("github-star-notice")).toBeTruthy();
});

it("suppresses the prompt while Release Notes or update review is open", () => {
  useUiStore.setState({ whatsNewOpen: true });
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS + 1_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
});

it("suppresses the prompt while on the Overview workspace and hides it when returning there", () => {
  act(() => { useAppStore.getState().setActiveWorkspace("overview"); });
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS + 1_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();

  act(() => { useAppStore.getState().setActiveWorkspace("map"); });
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  expect(screen.getByTestId("github-star-notice")).toBeTruthy();

  act(() => { useAppStore.getState().setActiveWorkspace("overview"); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
});

it("automatically stars via Tauri API on click, thanks the user, and never prompts again", async () => {
  let resolveInvoke: ((value: unknown) => void) | undefined;
  const invoke = vi.fn().mockImplementation(() => new Promise((resolve) => { resolveInvoke = resolve; }));
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  const view = render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });

  const starButton = screen.getByRole<HTMLButtonElement>("button", { name: "Star on GitHub" });
  await act(async () => {
    fireEvent.click(starButton);
    await vi.dynamicImportSettled();
  });
  expect(invoke).toHaveBeenCalledExactlyOnceWith("star_github_repository", {}, undefined);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Starring…" }).disabled).toBe(true);

  await act(async () => {
    resolveInvoke?.({ status: "starred", url: GITHUB_REPO_URL });
    await Promise.resolve();
  });
  expect(useUiStore.getState().githubStar).toEqual({ status: "starred", snoozedUntil: null });
  expect(screen.getByText("Thanks for starring SHARPpy Reimagined!")).toBeTruthy();

  act(() => { vi.advanceTimersByTime(3_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();

  // Remounting and interacting again stays suppressed forever after starring.
  view.unmount();
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS + 10_000); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
});

it("records a 14-day snooze when Tauri falls back to opening the browser", async () => {
  const invoke = vi.fn().mockResolvedValue({ status: "openedBrowser", url: GITHUB_REPO_URL });
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });

  const startedAt = Date.now();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Star on GitHub" }));
    await vi.dynamicImportSettled();
  });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  expect(useUiStore.getState().githubStar).toEqual({
    status: "snoozed",
    snoozedUntil: startedAt + GITHUB_STAR_BROWSER_SNOOZE_MS,
  });
});

it("opens the GitHub repository in a new tab in plain browser builds", async () => {
  const open = vi.fn();
  vi.stubGlobal("open", open);
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Star on GitHub" }));
    await Promise.resolve();
  });
  expect(open).toHaveBeenCalledExactlyOnceWith(GITHUB_REPO_URL, "_blank", "noopener,noreferrer");
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  expect(useUiStore.getState().githubStar.status).toBe("snoozed");
});

it("handles Not Now snooze expiry and permanent Don’t ask again dismissal", () => {
  const first = render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  const clickedAt = Date.now();
  fireEvent.click(screen.getByRole("button", { name: "Not Now" }));
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  expect(useUiStore.getState().githubStar).toEqual({
    status: "snoozed",
    snoozedUntil: clickedAt + GITHUB_STAR_NOT_NOW_MS,
  });
  first.unmount();

  // Still suppressed before 7 days pass.
  vi.setSystemTime(clickedAt + GITHUB_STAR_NOT_NOW_MS - 1_000);
  const early = render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  early.unmount();

  // Eligible again once the snooze expires, then permanently dismissed via X.
  vi.setSystemTime(clickedAt + GITHUB_STAR_NOT_NOW_MS);
  const resumed = render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  expect(screen.getByTestId("github-star-notice")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Don’t ask again" }));
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
  expect(useUiStore.getState().githubStar).toEqual({ status: "dismissed", snoozedUntil: null });
  resumed.unmount();

  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });
  expect(screen.queryByTestId("github-star-notice")).toBeNull();
});

it("shows a direct fallback link when the backend star action errors without marking dismissed", async () => {
  const invoke = vi.fn().mockRejectedValue(new Error("Could not launch browser"));
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  render(<GitHubStarNotice />);
  interact(REQUIRED_INTERACTIONS);
  act(() => { vi.advanceTimersByTime(INACTIVITY_DELAY_MS); });

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Star on GitHub" }));
    await vi.dynamicImportSettled();
  });
  expect(screen.getByText("Couldn’t star via GitHub right now.")).toBeTruthy();
  const link = screen.getByRole<HTMLAnchorElement>("link");
  expect(link.href).toBe(GITHUB_REPO_URL);
  expect(useUiStore.getState().githubStar).toEqual(DEFAULT_GITHUB_STAR_PREFERENCE);
  expect(useLogStore.getState().entries.at(-1)?.source).toBe("github-star");
});
