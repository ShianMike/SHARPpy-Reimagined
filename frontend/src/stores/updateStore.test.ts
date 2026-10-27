// @vitest-environment jsdom
import { Update } from "@tauri-apps/plugin-updater";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { appendEdit, INITIAL_VIEW } from "../features/sounding/viewHistory";
import { parseSoundingSummary } from "../lib/contracts/soundings";
import { useAppStore } from "./appStore";
import { currentPreferences, PREFERENCES_KEY } from "./preferences";
import { useSoundingStore } from "./soundingStore";
import { useUiStore } from "./uiStore";
import { useUpdateStore } from "./updateStore";

const check = vi.hoisted(() => vi.fn());
vi.mock("../lib/backend/tauriTransport", () => ({ checkForUpdates: check }));

const base = parseSoundingSummary(summaryExample);
const edited = appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]);
const work = [
  { name: "profile edits", view: edited },
  { name: "redo after undo to the original", view: { ...edited, cursor: 0 } },
  { name: "history after resetting the profile", view: appendEdit(edited, []) },
  { name: "chart options only", view: { ...INITIAL_VIEW, options: { chart: "winter" as const } } },
];

function availableUpdate() {
  const update = new Update({ rid: 1, currentVersion: "3.4.0", version: "3.4.1", rawJson: {} });
  const download = vi.spyOn(update, "download").mockResolvedValue();
  const install = vi.spyOn(update, "install").mockResolvedValue();
  useUpdateStore.setState({ update, status: "available" });
  return { download, install };
}

beforeEach(() => {
  useUpdateStore.setState(useUpdateStore.getInitialState(), true);
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useUiStore.setState({ lastUpdateCheckWindow: null });
  localStorage.removeItem(PREFERENCES_KEY);
  check.mockReset();
});
afterEach(() => { vi.restoreAllMocks(); });

it.each(work)("requires review and keeps $name at an inactive sounding time on cancel", async ({ view }) => {
  const { download, install } = availableUpdate();
  const sounding = useSoundingStore.getState();
  sounding.add({ ...base, soundingId: "multi", timeCount: 3 });
  sounding.setView("multi:2", view);
  sounding.add({ ...base, soundingId: "active" });
  const before = useSoundingStore.getState();
  useUpdateStore.getState().install();
  expect(useUpdateStore.getState()).toMatchObject({ status: "reviewing", installReview: "download" });
  useUpdateStore.getState().install();
  await useUpdateStore.getState().check();
  await useUpdateStore.getState().resolveInstallReview(false);
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(useUpdateStore.getState()).toMatchObject({ status: "available", installReview: null });
  expect(useSoundingStore.getState()).toBe(before);
  expect(download).not.toHaveBeenCalled();
  expect(install).not.toHaveBeenCalled();
  expect(check).not.toHaveBeenCalled();
  expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
});

it.each([false, true])("installs only after approval and flushes current preferences (existing work: %s)", async (hasWork) => {
  const { download, install } = availableUpdate();
  useSoundingStore.getState().add({ ...base, soundingId: "profile" });
  if (hasWork) useSoundingStore.getState().setView("profile", edited);
  const before = useSoundingStore.getState();
  useUpdateStore.getState().install();
  expect(download).not.toHaveBeenCalled();
  download.mockImplementation(() => {
    useAppStore.getState().setPendingStation("KOUN");
    return Promise.resolve();
  });
  install.mockImplementation(() => {
    expect(JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "null")).toEqual(currentPreferences());
    return Promise.resolve();
  });
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(download).toHaveBeenCalledOnce();
  expect(install).toHaveBeenCalledOnce();
  expect(JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "null")).toEqual(currentPreferences());
  expect(useUpdateStore.getState()).toMatchObject({ status: "installing", installReview: null });
  expect(useSoundingStore.getState()).toBe(before);
  await useUpdateStore.getState().resolveInstallReview(true);
  useUpdateStore.getState().install();
  expect(install).toHaveBeenCalledOnce();
});

it.each(work)("rechecks new $name during download and preserves it on cancellation", async ({ view }) => {
  const { download, install } = availableUpdate();
  useSoundingStore.getState().add({ ...base, soundingId: "multi", timeCount: 3 });
  let finish: (() => void) | undefined;
  download.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
  useUpdateStore.getState().install();
  const pending = useUpdateStore.getState().resolveInstallReview(true);
  useSoundingStore.getState().setView("multi:2", view);
  useSoundingStore.getState().add({ ...base, soundingId: "active" });
  const before = useSoundingStore.getState();
  useUpdateStore.getState().install();
  await useUpdateStore.getState().resolveInstallReview(true);
  await useUpdateStore.getState().check();
  expect(download).toHaveBeenCalledOnce();
  finish?.();
  await pending;
  expect(useUpdateStore.getState()).toMatchObject({ status: "reviewing", installReview: "install" });
  expect(install).not.toHaveBeenCalled();
  expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
  await useUpdateStore.getState().check();
  expect(check).not.toHaveBeenCalled();
  await useUpdateStore.getState().resolveInstallReview(false);
  expect(useUpdateStore.getState()).toMatchObject({ status: "available", installReview: null });
  expect(useSoundingStore.getState()).toBe(before);
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(install).not.toHaveBeenCalled();
});

it("requires fresh approval for changes to already reviewed work, then installs without another download", async () => {
  const { download, install } = availableUpdate();
  const sounding = useSoundingStore.getState();
  sounding.add({ ...base, soundingId: "profile" });
  sounding.setView("profile", edited);
  download.mockImplementation(() => {
    sounding.setView("profile", appendEdit(edited, [{ index: 0, values: { tmpc: 31 } }]));
    return Promise.resolve();
  });
  useUpdateStore.getState().install();
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(install).not.toHaveBeenCalled();
  expect(useUpdateStore.getState().installReview).toBe("install");
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(download).toHaveBeenCalledOnce();
  expect(install).toHaveBeenCalledOnce();
  expect(localStorage.getItem(PREFERENCES_KEY)).not.toBeNull();
});

it("does not install a failed download or consume approval on a retry", async () => {
  const { download, install } = availableUpdate();
  download.mockRejectedValueOnce(new Error("signature verification failed"));
  useUpdateStore.getState().install();
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(useUpdateStore.getState().status).toBe("error");
  expect(install).not.toHaveBeenCalled();
  expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
  useUpdateStore.getState().install();
  expect(useUpdateStore.getState().installReview).toBe("download");
  expect(download).toHaveBeenCalledOnce();
  await useUpdateStore.getState().resolveInstallReview(false);
  useUpdateStore.getState().install();
  expect(useUpdateStore.getState().installReview).toBe("download");
  expect(install).not.toHaveBeenCalled();
});

it("keeps sounding work and stops installation when preferences cannot be saved to disk", async () => {
  const { download, install } = availableUpdate();
  useSoundingStore.getState().add({ ...base, soundingId: "profile" });
  useSoundingStore.getState().setView("profile", edited);
  const before = useSoundingStore.getState();
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const save = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
  useUpdateStore.getState().install();
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(download).toHaveBeenCalledOnce();
  expect(install).not.toHaveBeenCalled();
  expect(useUpdateStore.getState()).toMatchObject({ status: "error", installReview: null, error: "Couldn’t save app preferences. Free some device storage, then try the update again. Your sounding work is still open." });
  expect(useSoundingStore.getState()).toBe(before);
  save.mockRestore();
  useUpdateStore.getState().install();
  await useUpdateStore.getState().resolveInstallReview(true);
  expect(install).toHaveBeenCalledOnce();
  expect(localStorage.getItem(PREFERENCES_KEY)).not.toBeNull();
});
