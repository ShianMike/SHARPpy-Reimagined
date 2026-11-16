import { create } from "zustand";
import type { RecoverySession } from "../features/sounding/recoverySession";

interface RecoveryState {
  status: "starting" | "idle" | "saving" | "saved" | "restoring" | "error" | "unavailable";
  previous: RecoverySession | null;
  prompt: boolean;
  savedAt: number | null;
  message: string | null;
  restore: () => Promise<void>;
  discard: () => Promise<void>;
  retrySave: (() => void) | null;
  backendRestart: () => void;
}
export const useSoundingRecoveryStore = create<RecoveryState>()(() => ({
  status: "starting", previous: null, prompt: false, savedAt: null, message: null,
  restore: () => Promise.resolve(), discard: () => Promise.resolve(), retrySave: null, backendRestart: () => undefined,
}));
