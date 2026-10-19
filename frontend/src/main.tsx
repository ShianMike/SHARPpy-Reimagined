// The boot sequence (DESIGN-frontend F2, F6), in this order:
// 1. probe the graphics device and select the profile;
// 2. write <html data-graphics-profile> before React renders, so the glass
//    tokens match the profile at first paint;
// 3. pick the transport by build mode (Vite folds the condition away, so the
//    desktop bundle holds neither the fixture transport nor its catalogue);
// 4. create the QueryClient, then render BackendProvider and <App/>.
// Before all of these, the stored preferences become the stores' initial values.
// A desktop build opened in a plain browser gets a full-window notice instead.

import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { version } from "../package.json";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/glass.css";
import "./styles/motion.css";
import { App, DesktopOnlyNotice } from "./app/App";
import { BackendClient } from "./lib/backend/client";
import { BackendProvider } from "./lib/backend/BackendProvider";
import { createQueryClient } from "./lib/backend/queryOptions";
import type { Transport } from "./lib/backend/transport";
import { probeGraphics, watchDevicePixelRatio } from "./lib/graphics/probe";
import { MARKS, markOnce } from "./lib/metrics";
import { useGraphicsStore } from "./stores/graphicsStore";
import { startSessionLog } from "./stores/logStore";
import { applyPreferences, createPreferencesRepository, DEFAULT_PREFERENCES, startPreferenceSync } from "./stores/preferences";
import { useUiStore } from "./stores/uiStore";
import type { GraphicsProfile } from "./types/graphics";

function startGraphics(): void {
  useGraphicsStore.getState().setCapabilities(probeGraphics());
  const writeProfile = (profile: GraphicsProfile): void => {
    document.documentElement.dataset.graphicsProfile = profile;
  };
  writeProfile(useGraphicsStore.getState().effective.profile);
  useGraphicsStore.subscribe((state) => state.effective.profile, writeProfile);
  // Automatic is re-evaluated when the device pixel ratio changes, not on resize.
  watchDevicePixelRatio((ratio) => {
    const capabilities = useGraphicsStore.getState().capabilities;
    if (capabilities !== null) {
      useGraphicsStore.getState().setCapabilities({ ...capabilities, devicePixelRatio: ratio });
    }
  });
}

async function loadTransport(): Promise<Transport> {
  const module =
    import.meta.env.MODE === "fixture"
      ? await import("./lib/backend/fixtureTransport")
      : import.meta.env.MODE === "live"
        ? await import("./lib/backend/httpTransport")
        : await import("./lib/backend/tauriTransport");
  return module.createTransport();
}

/** Sets sharppy:shell-painted once the first shell render is committed. */
function ShellPaintedMark(): null {
  useEffect(() => {
    requestAnimationFrame(() => {
      markOnce(MARKS.shellPainted);
    });
  }, []);
  return null;
}

function isDesktopBuildInBrowser(): boolean {
  return !["fixture", "live"].includes(import.meta.env.MODE) && !("__TAURI_INTERNALS__" in window);
}

/** Preferences first, so the graphics override applies to the first profile selection. */
function startPreferences(): boolean {
  const repository = createPreferencesRepository(() => window.localStorage);
  const preferences = repository.load();
  applyPreferences(preferences);
  const stop = startPreferenceSync(repository);
  // A pending debounced write is flushed when the page goes away.
  window.addEventListener("pagehide", stop, { once: true });
  return preferences !== DEFAULT_PREFERENCES;
}

async function boot(container: HTMLElement): Promise<void> {
  const stopLog = startSessionLog();
  window.addEventListener("pagehide", stopLog, { once: true });
  const returning = startPreferences();
  useUiStore.getState().prepareReleaseNotes(version, returning);
  startGraphics();
  const root = createRoot(container);
  if (isDesktopBuildInBrowser()) {
    root.render(<DesktopOnlyNotice />);
    return;
  }
  const client = new BackendClient(await loadTransport());
  const queryClient = createQueryClient();
  const tree: ReactNode = (
    <QueryClientProvider client={queryClient}>
      <BackendProvider client={client}>
        <App />
        <ShellPaintedMark />
      </BackendProvider>
    </QueryClientProvider>
  );
  root.render(<StrictMode>{tree}</StrictMode>);
}

const container = document.getElementById("root");
if (container === null) {
  throw new Error("index.html has no #root element");
}
void boot(container);
