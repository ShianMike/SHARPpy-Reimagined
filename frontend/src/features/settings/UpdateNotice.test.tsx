// @vitest-environment jsdom
import { Update } from "@tauri-apps/plugin-updater";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { version } from "../../../package.json";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLogStore } from "../../stores/logStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { AUTOMATIC_UPDATE_INTERVAL_MS, useUpdateStore } from "../../stores/updateStore";
import { useUiStore } from "../../stores/uiStore";
import { useAppStore, WORKSPACE_IDS } from "../../stores/appStore";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { appendEdit, INITIAL_VIEW } from "../sounding/viewHistory";
import { PointEditor } from "../sounding/PointEditor";
import { UpdateNotice } from "./UpdateNotice";

const check = vi.hoisted(() => vi.fn<() => Promise<Update | null>>());
vi.mock("../../lib/backend/tauriTransport", () => ({ checkForUpdates: check }));

function availableUpdate(targetVersion = "3.4.1") {
  const update = new Update({ rid: 1, currentVersion: version, version: targetVersion, rawJson: {} });
  return { update,
    close: vi.spyOn(update, "close").mockResolvedValue(),
    download: vi.spyOn(update, "download").mockResolvedValue(),
    install: vi.spyOn(update, "install").mockResolvedValue(),
  };
}

beforeEach(() => {
  useUiStore.setState({ lastUpdateCheckWindow: null });
  useUpdateStore.setState(useUpdateStore.getInitialState(), true);
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useLogStore.getState().clear();
  check.mockReset();
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("checks once on startup in StrictMode and installs only after Update now", async () => {
  const { update, download, install } = availableUpdate();
  let announce: ((update: Update) => void) | undefined;
  check.mockReturnValue(new Promise<Update>((resolve) => { announce = resolve; }));
  render(<StrictMode><UpdateNotice /></StrictMode>);
  await waitFor(() => { expect(check).toHaveBeenCalledOnce(); });
  await act(async () => { announce?.(update); await Promise.resolve(); });
  expect(screen.getByText("Version 3.4.1 is available")).toBeTruthy();
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
  let finish: (() => void) | undefined;
  download.mockImplementation((progress) => {
    progress?.({ event: "Started", data: { contentLength: 100 } });
    progress?.({ event: "Progress", data: { chunkLength: 25 } });
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  fireEvent.click(screen.getByRole("button", { name: "Update now" }));
  expect(screen.getByRole("dialog", { name: "Install app update?" })).toBeTruthy();
  expect(download).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Install update & restart" }));
  expect(screen.getByText("Downloading update: 25%")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Later" }).disabled).toBe(true);
  expect(install).not.toHaveBeenCalled();
  await act(async () => { finish?.(); await Promise.resolve(); });
  expect(install).toHaveBeenCalledOnce();
  expect(screen.getByText("Installing update…")).toBeTruthy();
});

it.each(WORKSPACE_IDS)("reviews installation from %s even when the notice was dismissed, and Escape preserves work", (workspace) => {
  const { update, download, install } = availableUpdate();
  useAppStore.setState({ activeWorkspace: workspace });
  useUiStore.setState({ lastUpdateCheckWindow: Math.floor(Date.now() / AUTOMATIC_UPDATE_INTERVAL_MS) * AUTOMATIC_UPDATE_INTERVAL_MS });
  useUpdateStore.setState({ status: "available", update, dismissedVersion: update.version });
  const sounding = useSoundingStore.getState();
  sounding.add({ ...parseSoundingSummary(summaryExample), soundingId: "multi", timeCount: 3 });
  sounding.setView("multi:2", { ...appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]), cursor: 0 });
  const before = useSoundingStore.getState();
  render(<UpdateNotice />);
  expect(screen.queryByTestId("app-update-notice")).toBeNull();
  act(() => { useUpdateStore.getState().install(); });
  const dialog = screen.getByRole("dialog", { name: "Discard sounding work and update?" });
  expect(screen.getByText(/Saved app preferences, source files and cached weather data on disk are kept/u)).toBeTruthy();
  expect(screen.getByText(/All open soundings, profile edits, chart changes and undo\/redo history/u)).toBeTruthy();
  expect(screen.getByText(/A PNG cannot restore editable profiles or undo\/redo history/u)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Discard work & install update" })).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(useSoundingStore.getState()).toBe(before);
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
  expect(check).not.toHaveBeenCalled();
});

it("returns to soundings for PNG export without downloading or discarding work", () => {
  const { update, download, install } = availableUpdate();
  useUiStore.setState({ lastUpdateCheckWindow: Math.floor(Date.now() / AUTOMATIC_UPDATE_INTERVAL_MS) * AUTOMATIC_UPDATE_INTERVAL_MS });
  useUpdateStore.setState({ status: "available", update });
  useAppStore.setState({ activeWorkspace: "settings" });
  const sounding = useSoundingStore.getState();
  sounding.add({ ...parseSoundingSummary(summaryExample), soundingId: "profile" });
  sounding.setView("profile", appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]));
  const before = useSoundingStore.getState();
  render(<UpdateNotice />);
  fireEvent.click(screen.getByRole("button", { name: "Update now" }));
  fireEvent.click(screen.getByRole("button", { name: "Return to soundings" }));
  expect(useAppStore.getState().activeWorkspace).toBe("sounding");
  expect(useSoundingStore.getState()).toBe(before);
  expect(useUpdateStore.getState().installReview).toBeNull();
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
});

it("warns about unapplied inputs even without ViewState edits and keeps the mounted editor on Cancel", () => {
  const { update, download, install } = availableUpdate();
  useUiStore.setState({ lastUpdateCheckWindow: Math.floor(Date.now() / AUTOMATIC_UPDATE_INTERVAL_MS) * AUTOMATIC_UPDATE_INTERVAL_MS });
  useUpdateStore.setState({ status: "available", update });
  useAppStore.setState({ activeWorkspace: "sounding" });
  render(<><PointEditor point={{ id: "tmpc:0", label: "Temperature", x: 0.5, y: 0.5,
    values: [{ key: "tmpc", label: "Temperature", value: 20, original: 20, unit: "°C", min: -100, max: 60 }] }}
    busy={false} onApply={() => undefined} /><UpdateNotice /></>);
  const input = screen.getByRole<HTMLInputElement>("spinbutton", { name: "Temperature °C" });
  fireEvent.change(input, { target: { value: "27" } });
  fireEvent.click(screen.getByRole("button", { name: "Update now" }));
  expect(screen.getByRole("dialog", { name: "Install app update?" })).toBeTruthy();
  expect(screen.getByText(/Unapplied level inputs and unfinished recalculations will also be lost/u)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(input.value).toBe("27");
  expect(useSoundingStore.getState().views).toEqual({});
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
});

it("shows a final discard confirmation for new work during download and Cancel keeps it", async () => {
  const { update, download, install } = availableUpdate();
  useUiStore.setState({ lastUpdateCheckWindow: Math.floor(Date.now() / AUTOMATIC_UPDATE_INTERVAL_MS) * AUTOMATIC_UPDATE_INTERVAL_MS });
  useUpdateStore.setState({ status: "available", update });
  let finish: (() => void) | undefined;
  download.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
  render(<UpdateNotice />);
  fireEvent.click(screen.getByRole("button", { name: "Update now" }));
  fireEvent.click(screen.getByRole("button", { name: "Install update & restart" }));
  act(() => {
    const sounding = useSoundingStore.getState();
    sounding.add({ ...parseSoundingSummary(summaryExample), soundingId: "profile" });
    sounding.setView("profile", appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]));
  });
  const before = useSoundingStore.getState();
  await act(async () => { finish?.(); await Promise.resolve(); });
  expect(screen.getByRole("dialog", { name: "Discard sounding work and update?" })).toBeTruthy();
  expect(screen.getByText(/Local sounding work changed during the download/u)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(useSoundingStore.getState()).toBe(before);
  expect(install).not.toHaveBeenCalled();
});

it("Later hides the same version across UTC windows but announces a newer version", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T00:00:00Z"));
  const first = availableUpdate(), repeat = availableUpdate(), newer = availableUpdate("3.4.2");
  check.mockResolvedValueOnce(first.update).mockResolvedValueOnce(repeat.update).mockResolvedValueOnce(newer.update);
  await act(async () => { render(<UpdateNotice />); await vi.dynamicImportSettled(); });
  fireEvent.click(screen.getByRole("button", { name: "Later" }));
  expect(screen.queryByRole("status")).toBeNull();
  expect(useUpdateStore.getState().update).toBe(first.update);
  expect(first.close).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000); });
  expect(check).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("status")).toBeNull();
  expect(first.close).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000); });
  expect(screen.getByText("Version 3.4.2 is available")).toBeTruthy();
  expect(first.download).not.toHaveBeenCalled();
  expect(repeat.download).not.toHaveBeenCalled();
  expect(newer.download).not.toHaveBeenCalled();
});

it("skips offline checks, retries on reconnection, and cleans up its timer and listener", async () => {
  vi.useFakeTimers();
  const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  check.mockResolvedValue(null);
  const view = render(<UpdateNotice />);
  await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60 * 1000); });
  expect(check).not.toHaveBeenCalled();
  expect(useUiStore.getState().lastUpdateCheckWindow).toBeNull();
  online.mockReturnValue(true);
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  view.unmount();
  await act(async () => { fireEvent(window, new Event("online")); await vi.advanceTimersByTimeAsync(60 * 60 * 1000); });
  expect(check).toHaveBeenCalledOnce();
});

it("keeps background failures quiet and preserves a known update for installation", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T00:00:00Z"));
  const { update, close } = availableUpdate();
  check.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(update).mockRejectedValueOnce(new Error("offline again"));
  await act(async () => { render(<UpdateNotice />); await vi.dynamicImportSettled(); });
  expect(useUpdateStore.getState().status).toBe("idle");
  expect(check).toHaveBeenCalledOnce();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(useLogStore.getState().entries[0]?.detail).toContain("offline");
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000); });
  expect(screen.getByText("Version 3.4.1 is available")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(useUpdateStore.getState().update).toBe(update);
  expect(useUpdateStore.getState().status).toBe("available");
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Update now" }).disabled).toBe(false);
});

it("does not check or announce desktop updates in a browser", () => {
  vi.unstubAllGlobals();
  render(<UpdateNotice />);
  fireEvent(window, new Event("online"));
  expect(screen.queryByRole("status")).toBeNull();
  expect(check).not.toHaveBeenCalled();
});

it("aligns automatic checks with 12:00 and 00:00 UTC and ignores repeated online/visibility events", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T11:59:59.999Z"));
  check.mockResolvedValue(null);
  const view = render(<StrictMode><UpdateNotice /></StrictMode>);
  await act(async () => { await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  await act(async () => {
    fireEvent(window, new Event("online"));
    fireEvent(document, new Event("visibilitychange"));
    await vi.dynamicImportSettled();
  });
  expect(check).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(check).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000 - 1); });
  expect(check).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(check).toHaveBeenCalledTimes(3);
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000); });
  expect(check).toHaveBeenCalledTimes(3);
});

it("remembers the current check window across reloads while manual checks remain available", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T04:00:00Z"));
  check.mockResolvedValue(null);
  const view = render(<UpdateNotice />);
  await act(async () => { await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  const saved = localStorage.getItem("sharppy-workspace-ui");
  expect(saved).not.toBeNull();
  expect(useUiStore.getState().lastUpdateCheckWindow).toBe(Date.parse("2026-10-25T00:00:00Z"));
  view.unmount();
  useUiStore.setState({ lastUpdateCheckWindow: null });
  localStorage.setItem("sharppy-workspace-ui", saved ?? "");
  await act(async () => { await useUiStore.persist.rehydrate(); });
  useUpdateStore.setState({ status: "idle" });
  render(<UpdateNotice />);
  await act(async () => { await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  await act(async () => { await useUpdateStore.getState().check(); });
  await act(async () => { await useUpdateStore.getState().check(); });
  expect(check).toHaveBeenCalledTimes(3);
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledTimes(3);
});

it("checks only the current UTC window after waking from several days asleep", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T04:00:00Z"));
  check.mockResolvedValue(null);
  const view = render(<UpdateNotice />);
  await act(async () => { await vi.dynamicImportSettled(); });
  vi.setSystemTime(new Date("2026-10-29T15:00:00Z"));
  await act(async () => { fireEvent(document, new Event("visibilitychange")); await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledTimes(2);
  expect(useUiStore.getState().lastUpdateCheckWindow).toBe(Date.parse("2026-10-29T12:00:00Z"));
  view.unmount();
});
