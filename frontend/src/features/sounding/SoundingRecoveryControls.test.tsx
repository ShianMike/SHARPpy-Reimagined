// @vitest-environment jsdom
import summaryFixture from "@contracts/public/soundings/summary.example.json";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { useUiStore } from "../../stores/uiStore";
import { SoundingRecoveryPrompt, SoundingRecoveryStatus } from "./SoundingRecoveryControls";

afterEach(() => { cleanup(); useSoundingRecoveryStore.setState(useSoundingRecoveryStore.getInitialState(), true); useUiStore.setState({ whatsNewOpen: false }); });
it("offers restoration, preserves a deferred session, and reopens the prompt from sounding controls", () => {
  const restore = vi.fn(() => Promise.resolve()), discard = vi.fn(() => Promise.resolve());
  useSoundingRecoveryStore.setState({ status: "idle", prompt: true, restore, discard, previous: {
    version: 1, savedAt: 1, open: [parseSoundingSummary(summaryFixture)], activeId: summaryFixture.soundingId,
    mode: "winter", choice: "MU", views: {}, timeIndices: {},
  } });
  useUiStore.setState({ whatsNewOpen: true });
  render(<><SoundingRecoveryPrompt /><SoundingRecoveryStatus /></>);
  expect(screen.queryByRole("dialog")).toBeNull();
  act(() => { useUiStore.setState({ whatsNewOpen: false }); });
  expect(screen.getByRole("dialog", { name: "Restore previous session?" }).textContent).toContain("Winter view");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(useSoundingRecoveryStore.getState().previous).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Restore previous session" }));
  fireEvent.click(screen.getByRole("button", { name: "Restore session" }));
  expect(restore).toHaveBeenCalledOnce();
  expect(discard).not.toHaveBeenCalled();
});
it("reports failed saves without claiming the current session is protected", () => {
  const retrySave = vi.fn();
  useSoundingRecoveryStore.setState({ status: "error", message: "Storage full", retrySave });
  render(<SoundingRecoveryStatus />);
  expect(screen.getByText("Storage full")).toBeTruthy();
  expect(screen.queryByText("Recovery copy saved on this device")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry recovery save" }));
  expect(retrySave).toHaveBeenCalledOnce();
});
