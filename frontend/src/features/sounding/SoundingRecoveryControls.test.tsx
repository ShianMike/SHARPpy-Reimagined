// @vitest-environment jsdom
import summaryFixture from "@contracts/public/soundings/summary.example.json";
import multiTimeFixture from "@contracts/public/soundings/summary.multi-time.json";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { useUiStore } from "../../stores/uiStore";
import { SoundingRecoveryPrompt, SoundingRecoveryStatus } from "./SoundingRecoveryControls";

afterEach(() => { cleanup(); useSoundingRecoveryStore.setState(useSoundingRecoveryStore.getInitialState(), true); useUiStore.setState({ whatsNewOpen: false }); });
it("offers restoration, preserves a deferred session, and reopens the prompt from sounding controls", () => {
  const restore = vi.fn(() => {
    useSoundingRecoveryStore.setState({ status: "restoring", message: "Reopening saved profiles…" });
    return Promise.resolve();
  });
  const discard = vi.fn(() => Promise.resolve());
  const multiTime = parseSoundingSummary(multiTimeFixture);
  const undated = parseSoundingSummary({ ...summaryFixture, validTime: null });
  useSoundingRecoveryStore.setState({ status: "idle", prompt: true, restore, discard, previous: {
    version: 1, savedAt: 1, open: [multiTime, undated], activeId: multiTime.soundingId,
    mode: "winter", choice: "MU", views: {}, timeIndices: { [multiTime.soundingId]: 2 },
  } });
  useUiStore.setState({ whatsNewOpen: true });
  const { container } = render(<><SoundingRecoveryPrompt /><SoundingRecoveryStatus /></>);
  const footer = within(container);
  expect(screen.queryByRole("dialog")).toBeNull();
  act(() => { useUiStore.setState({ whatsNewOpen: false }); });
  expect(screen.getByRole("dialog", { name: "Restore sounding session?" }).textContent).toContain("Winter view");
  expect(screen.getByRole("list", { name: "2 soundings" })).toBeTruthy();
  expect(screen.getByText("Last viewed").closest("li")?.textContent).toContain("2026-06-25 08Z");
  expect(screen.getByText("Undated")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Not now" }));
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(useSoundingRecoveryStore.getState().previous).not.toBeNull();
  expect(footer.getByRole("button", { name: "Restore previous session" })).toBeTruthy();
  expect(footer.queryByRole("status")).toBeNull();
  act(() => { useSoundingRecoveryStore.setState({ status: "saved" }); });
  expect(footer.getByRole("status").textContent).toBe("Current session saved");
  fireEvent.click(footer.getByRole("button", { name: "Restore previous session" }));
  fireEvent.click(screen.getByRole("button", { name: "Restore session" }));
  expect(restore).toHaveBeenCalledOnce();
  expect(discard).not.toHaveBeenCalled();
  expect(footer.getByRole("status").textContent).toBe("Restoring session…");
});
it("shows only one live status line as autosave starts and saves the session", () => {
  const { container } = render(<SoundingRecoveryStatus />);
  const transitions = [
    ["starting", "Starting autosave…"],
    ["idle", "Autosave on"],
    ["saving", "Saving session…"],
    ["saved", "Session saved locally"],
  ] as const;

  for (const [status, label] of transitions) {
    act(() => { useSoundingRecoveryStore.setState({ status }); });
    expect(screen.getByRole("status").textContent).toBe(label);
    expect(container.textContent).toBe(label);
  }
});
it("reports failed saves without claiming the current session is protected", () => {
  const retrySave = vi.fn();
  useSoundingRecoveryStore.setState({ status: "error", message: "Storage full", retrySave });
  render(<SoundingRecoveryStatus />);
  expect(screen.getByText("Storage full")).toBeTruthy();
  expect(screen.queryByText("Session saved locally")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry recovery save" }));
  expect(retrySave).toHaveBeenCalledOnce();
});
