import type { Update } from "@tauri-apps/plugin-updater";
import { create } from "zustand";
import { recordLog } from "./logStore";
import { createPreferencesRepository, currentPreferences } from "./preferences";

interface UpdateState {
  status: "idle" | "checking" | "current" | "available" | "downloading" | "installing" | "error";
  update: Update | null;
  downloaded: number;
  total: number | undefined;
  error: string | null;
  dismissedVersion: string | null;
  dismiss: () => void;
  check: (automatic?: boolean) => Promise<void>;
  install: () => Promise<void>;
}

const busy = (status: UpdateState["status"]): boolean => status === "checking" || status === "downloading" || status === "installing";

// Keep an in-progress update alive when the user leaves Settings.
export const useUpdateStore = create<UpdateState>((set, get) => ({
  status: "idle", update: null, downloaded: 0, total: undefined, error: null, dismissedVersion: null,
  dismiss: () => { set({ dismissedVersion: get().update?.version ?? null }); },
  check: async (automatic = false) => {
    if (busy(get().status)) return;
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
  install: async () => {
    const { update, status } = get();
    if (update === null || busy(status)) return;
    set({ status: "downloading", error: null, downloaded: 0, total: undefined });
    try {
      await update.download((event) => {
        if (event.event === "Started") set({ total: event.data.contentLength || undefined });
        if (event.event === "Progress") set((state) => ({ downloaded: state.downloaded + event.data.chunkLength }));
      });
      // Windows exits directly to launch the installer; flush the debounced preferences first.
      createPreferencesRepository(() => window.localStorage).save(currentPreferences());
      set({ status: "installing" });
      await update.install();
    } catch (error) {
      recordLog("error", "updater", "Update installation failed", String(error));
      set({ status: "error", error: "Couldn’t install the update. Try again or check for a newer release." });
    }
  },
}));
