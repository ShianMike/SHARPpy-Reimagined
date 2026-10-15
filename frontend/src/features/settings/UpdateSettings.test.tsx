// @vitest-environment jsdom
import { Update, type DownloadEvent } from "@tauri-apps/plugin-updater";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { version } from "../../../package.json";
import { useLogStore } from "../../stores/logStore";
import { PREFERENCES_KEY } from "../../stores/preferences";
import { useUpdateStore } from "../../stores/updateStore";
import { UpdateSettings } from "./UpdateSettings";

const check = vi.hoisted(() => vi.fn<() => Promise<Update | null>>());
vi.mock("../../lib/backend/tauriTransport", () => ({ checkForUpdates: check }));

function availableUpdate() {
  const update = new Update({ rid: 1, currentVersion: "3.0.0", version: "3.1.0", body: "New map tools", rawJson: {} });
  return { update,
    close: vi.spyOn(update, "close").mockResolvedValue(),
    download: vi.spyOn(update, "download").mockResolvedValue(),
    install: vi.spyOn(update, "install").mockResolvedValue(),
  };
}

beforeEach(() => {
  useUpdateStore.setState({ status: "idle", update: null, downloaded: 0, total: undefined, error: null, dismissedVersion: null });
  useLogStore.getState().clear();
  localStorage.removeItem(PREFERENCES_KEY);
  check.mockReset();
  vi.stubGlobal("__TAURI_INTERNALS__", {});
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shows a newer version and downloads only after an explicit install click, preserving preferences", async () => {
  const { update, download, install } = availableUpdate();
  let progress: ((event: DownloadEvent) => void) | undefined;
  let finish: (() => void) | undefined;
  download.mockImplementation((handler) => {
    progress = handler;
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  check.mockResolvedValue(update);
  const view = render(<UpdateSettings />);
  expect(screen.getByText(`Current version: ${version}`)).toBeTruthy();
  expect(check).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await screen.findByText("Version 3.1.0 is available");
  expect(download).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Install update" }));
  act(() => {
    progress?.({ event: "Started", data: { contentLength: 100 } });
    progress?.({ event: "Progress", data: { chunkLength: 25 } });
  });
  expect(screen.getByRole("progressbar").getAttribute("value")).toBe("25");
  expect(screen.getByText("25%")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check for updates" }).disabled).toBe(true);
  // Navigation must not cancel the download or allow a second installation.
  view.unmount();
  render(<UpdateSettings />);
  expect(screen.getByText("25%")).toBeTruthy();
  await useUpdateStore.getState().check();
  await useUpdateStore.getState().install();
  expect(check).toHaveBeenCalledTimes(1);
  expect(download).toHaveBeenCalledTimes(1);
  expect(install).not.toHaveBeenCalled();
  await act(async () => { finish?.(); await Promise.resolve(); });
  await waitFor(() => { expect(install).toHaveBeenCalledOnce(); });
  expect(localStorage.getItem(PREFERENCES_KEY)).not.toBeNull();
  expect(screen.getByText("Installing update…")).toBeTruthy();
});

it("reports up to date, closes an unused update on recheck, and allows retry after a failed check", async () => {
  const { update, close } = availableUpdate();
  check.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(update).mockResolvedValueOnce(null);
  render(<UpdateSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("You’re up to date")).toBeNull();
  expect(useLogStore.getState().entries[0]?.detail).toContain("offline");
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await screen.findByText("Version 3.1.0 is available");
  close.mockRejectedValueOnce(new Error("resource already closed"));
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await screen.findByText("You’re up to date");
  expect(close).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "Install update" })).toBeNull();
});

it("does not install a failed or unverified download and can retry with an unknown download size", async () => {
  const { update, download, install } = availableUpdate();
  download.mockRejectedValueOnce(new Error("signature verification failed"));
  check.mockResolvedValue(update);
  render(<UpdateSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await screen.findByText("Version 3.1.0 is available");
  fireEvent.click(screen.getByRole("button", { name: "Install update" }));
  await screen.findByRole("alert");
  expect(install).not.toHaveBeenCalled();
  expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
  let finish: (() => void) | undefined;
  download.mockImplementation((progress) => {
    progress?.({ event: "Started", data: {} });
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  fireEvent.click(screen.getByRole("button", { name: "Install update" }));
  expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  await act(async () => { finish?.(); await Promise.resolve(); });
  await waitFor(() => { expect(install).toHaveBeenCalledOnce(); });
});

it("explains the desktop requirement in the browser without contacting an update server", () => {
  vi.unstubAllGlobals();
  render(<UpdateSettings />);
  expect(screen.getByText("Check for updates in the installed Windows app.")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check for updates" }).disabled).toBe(true);
  expect(check).not.toHaveBeenCalled();
});
