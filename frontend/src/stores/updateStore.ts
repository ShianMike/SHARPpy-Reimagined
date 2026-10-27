import type { Update } from "@tauri-apps/plugin-updater";
import { create } from "zustand";
import { hasLocalWork } from "../features/sounding/viewHistory";
import { recordLog } from "./logStore";
import { createPreferencesRepository, currentPreferences } from "./preferences";
import { useSoundingStore } from "./soundingStore";
import { useUiStore } from "./uiStore";

export const AUTOMATIC_UPDATE_INTERVAL_MS = 12 * 60 * 60 * 1000;

interface UpdateState {
  status: "idle" | "checking" | "current" | "available" | "reviewing" | "downloading" | "installing" | "error";
  installReview: "download" | "install" | null;
  soundingWorkChanged: boolean;
  update: Update | null;
  downloaded: number;
  total: number | undefined;
  error: string | null;
  dismissedVersion: string | null;
  dismiss: () => void;
  check: (automatic?: boolean) => Promise<void>;
  install: () => void;
  noteSoundingWork: () => void;
  resolveInstallReview: (proceed: boolean) => Promise<void>;
}

const busy = (status: UpdateState["status"]): boolean => status === "checking" || status === "reviewing" || status === "downloading" || status === "installing";

// Keep an in-progress update alive when the user leaves Settings.
export const useUpdateStore = create<UpdateState>((set, get) => ({
  status: "idle", installReview: null, soundingWorkChanged: false, update: null, downloaded: 0, total: undefined, error: null, dismissedVersion: null,
  dismiss: () => { set({ dismissedVersion: get().update?.version ?? null }); },
  noteSoundingWork: () => { if (get().status === "downloading" && !get().soundingWorkChanged) set({ soundingWorkChanged: true }); },
  check: async (automatic = false) => {
    if (busy(get().status)) return;
    if (automatic && !navigator.onLine) return;
    const windowStart = Math.floor(Date.now() / AUTOMATIC_UPDATE_INTERVAL_MS) * AUTOMATIC_UPDATE_INTERVAL_MS;
    if (automatic && useUiStore.getState().lastUpdateCheckWindow === windowStart) return;
    // Record attempts before the request so failures and restarts cannot repeat this window.
    useUiStore.getState().setLastUpdateCheckWindow(windowStart);
    const previous = get().update;
    set({ status: "checking", error: null, downloaded: 0, total: undefined });
    try {
      const { checkForUpdates } = await import("../lib/backend/tauriTransport");
      const update = await checkForUpdates({ timeout: 30_000 });
      await previous?.close().catch((error: unknown) => {
        recordLog("warning", "updater", "Could not release the previous update", String(error));
      });
      set({ update, status: update === null ? "current" : "available" });
    } catch (error) {
      recordLog("error", "updater", "Update check failed", String(error));
      set({ status: automatic ? (previous === null ? "idle" : "available") : "error",
        error: automatic ? null : "Couldn’t check for updates. Check your connection and try again." });
    }
  },
  install: () => {
    const { update, status } = get();
    if (update === null || busy(status)) return;
    set({ status: "reviewing", installReview: "download", error: null });
  },
  resolveInstallReview: async (proceed) => {
    const { update, status, installReview } = get();
    if (update === null || status !== "reviewing" || installReview === null) return;
    if (!proceed) {
      set({ status: "available", installReview: null });
      return;
    }
    const reviewedViews = useSoundingStore.getState().views;
    set({ status: installReview === "download" ? "downloading" : "installing", installReview: null });
    try {
      if (installReview === "download") {
        set({ downloaded: 0, total: undefined, soundingWorkChanged: false });
        await update.download((event) => {
          if (event.event === "Started") set({ total: event.data.contentLength || undefined });
          if (event.event === "Progress") set((state) => ({ downloaded: state.downloaded + event.data.chunkLength }));
        });
        // Approval covers the reviewed work, not edits made while the download was running.
        if (get().soundingWorkChanged || Object.entries(useSoundingStore.getState().views).some(([id, view]) => hasLocalWork(view) && view !== reviewedViews[id])) {
          set({ status: "reviewing", installReview: "install" });
          return;
        }
      }
      // Windows exits directly to launch the installer; flush the debounced preferences first.
      if (!createPreferencesRepository(() => window.localStorage).save(currentPreferences())) {
        recordLog("error", "updater", "App preferences could not be saved before update installation");
        set({ status: "error", error: "Couldn’t save app preferences. Free some device storage, then try the update again. Your sounding work is still open." });
        return;
      }
      set({ status: "installing" });
      await update.install();
    } catch (error) {
      recordLog("error", "updater", "Update installation failed", String(error));
      set({ status: "error", error: "Couldn’t install the update. Try again or check for a newer release." });
    }
  },
}));
