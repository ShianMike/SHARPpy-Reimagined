// @vitest-environment jsdom
import { Update } from "@tauri-apps/plugin-updater";
import { version } from "../../../package.json";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLogStore } from "../../stores/logStore";
import { useUpdateStore } from "../../stores/updateStore";
import { UpdateNotice } from "./UpdateNotice";

const check = vi.hoisted(() => vi.fn<() => Promise<Update | null>>());
vi.mock("../../lib/backend/tauriTransport", () => ({ checkForUpdates: check }));

function availableUpdate(targetVersion = "3.3.0") {
  const update = new Update({ rid: 1, currentVersion: version, version: targetVersion, rawJson: {} });
  return { update,
    close: vi.spyOn(update, "close").mockResolvedValue(),
    download: vi.spyOn(update, "download").mockResolvedValue(),
    install: vi.spyOn(update, "install").mockResolvedValue(),
  };
}

beforeEach(() => {
  useUpdateStore.setState({ status: "idle", update: null, downloaded: 0, total: undefined, error: null, dismissedVersion: null });
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
  expect(screen.getByText("Version 3.3.0 is available")).toBeTruthy();
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
  let finish: (() => void) | undefined;
  download.mockImplementation((progress) => {
    progress?.({ event: "Started", data: { contentLength: 100 } });
    progress?.({ event: "Progress", data: { chunkLength: 25 } });
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  fireEvent.click(screen.getByRole("button", { name: "Update now" }));
  expect(screen.getByText("Downloading update: 25%")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Later" }).disabled).toBe(true);
  expect(install).not.toHaveBeenCalled();
  await act(async () => { finish?.(); await Promise.resolve(); });
  expect(install).toHaveBeenCalledOnce();
  expect(screen.getByText("Installing update…")).toBeTruthy();
});

it("Later hides the same version across hourly checks but announces a newer version", async () => {
  vi.useFakeTimers();
  const first = availableUpdate(), repeat = availableUpdate(), newer = availableUpdate("3.4.0");
  check.mockResolvedValueOnce(first.update).mockResolvedValueOnce(repeat.update).mockResolvedValueOnce(newer.update);
  await act(async () => { render(<UpdateNotice />); await vi.dynamicImportSettled(); });
  fireEvent.click(screen.getByRole("button", { name: "Later" }));
  expect(screen.queryByRole("status")).toBeNull();
  expect(useUpdateStore.getState().update).toBe(first.update);
  expect(first.close).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60 * 1000); });
  expect(check).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("status")).toBeNull();
  expect(first.close).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60 * 1000); });
  expect(screen.getByText("Version 3.4.0 is available")).toBeTruthy();
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
  online.mockReturnValue(true);
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
  expect(check).toHaveBeenCalledOnce();
  view.unmount();
  await act(async () => { fireEvent(window, new Event("online")); await vi.advanceTimersByTimeAsync(60 * 60 * 1000); });
  expect(check).toHaveBeenCalledOnce();
});

it("keeps background failures quiet and preserves a known update for installation", async () => {
  const { update, close } = availableUpdate();
  check.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(update).mockRejectedValueOnce(new Error("offline again"));
  render(<UpdateNotice />);
  await waitFor(() => { expect(useUpdateStore.getState().status).toBe("idle"); expect(check).toHaveBeenCalledOnce(); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(useLogStore.getState().entries[0]?.detail).toContain("offline");
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
  expect(screen.getByText("Version 3.3.0 is available")).toBeTruthy();
  await act(async () => { fireEvent(window, new Event("online")); await vi.dynamicImportSettled(); });
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
