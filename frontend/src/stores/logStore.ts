import { create } from "zustand";
import { diagnosticMetadata, type DiagnosticInput, type DiagnosticMetadata } from "../lib/diagnostics";

export type LogLevel = "info" | "warning" | "error";
export interface LogEntry {
  id: number;
  time: string;
  level: LogLevel;
  source: string;
  message: string;
  detail: string;
  diagnostic?: DiagnosticMetadata;
}

export const MAX_LOG_ENTRIES = 500;
// shortcut: keep a bounded log for this session; use the desktop log file for older backend history.
export const useLogStore = create<{ entries: readonly LogEntry[]; nextId: number; clear: () => void }>((set) => ({
  entries: [], nextId: 1,
  clear: () => { set({ entries: [] }); },
}));

export function recordLog(level: LogLevel, source: string, message: string, detail = "", diagnostic?: DiagnosticInput): void {
  useLogStore.setState((state) => ({
    nextId: state.nextId + 1,
    entries: [{ id: state.nextId, time: new Date().toISOString(), level, source, message: message.slice(0, 4000), detail: detail.slice(0, 8000), ...(level === "info" ? {} : { diagnostic: diagnosticMetadata(source, diagnostic) }) }, ...state.entries].slice(0, MAX_LOG_ENTRIES),
  }));
}

/** Install once at startup, before requests and rendering can fail. */
export function startSessionLog(): () => void {
  recordLog("info", "Workspace", "Session started");
  const onError = (event: ErrorEvent): void => { recordLog("error", "Application", event.message || "An application error occurred", event.filename ? `${event.filename}:${String(event.lineno)}` : ""); };
  const onRejection = (event: PromiseRejectionEvent): void => {
    // Backend failures have already been recorded by the shared client.
    if (event.reason instanceof Error && event.reason.name === "BackendFailure") return;
    recordLog("error", "Application", event.reason instanceof Error ? event.reason.message : "An operation failed unexpectedly");
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => { window.removeEventListener("error", onError); window.removeEventListener("unhandledrejection", onRejection); };
}
