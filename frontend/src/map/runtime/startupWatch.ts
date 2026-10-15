// The map start-up watch (DESIGN-frontend F4). A small state object with no
// MapLibre or DOM import: MapRuntime feeds it map events and injects a
// visibility source and a clock.
//
// - The 15 s deadline counts visible time only: browsers pause
//   requestAnimationFrame in hidden pages, and MapLibre draws only from it.
// - Basemap sources are loaded, failed (an error event; it stays failed), or
//   slow (still pending when the deadline fires). Both issue sets are reported
//   whenever one changes.
// - The first deck.gl render sets the renderer ready; an overlay error before
//   it, or the deadline with no render, sets it failed.

import { MARKS, markOnce } from "../../lib/metrics";

export const MAP_STARTUP_TIMEOUT_MS = 15_000;

export interface VisibilitySource {
  isVisible(): boolean;
  /** Calls the listener on every visibility change; returns the unsubscribe function. */
  onChange(listener: () => void): () => void;
}

export interface Clock {
  now(): number;
}

export type StartupFailureKind = "overlay" | "timeout";

export interface StartupWatchSink {
  /** Both issue sets, sorted, each time one of them changes. */
  basemapIssues: (failed: readonly string[], slow: readonly string[]) => void;
  rendererReady: () => void;
  rendererFailed: (kind: StartupFailureKind, message: string) => void;
}

export interface StartupWatchOptions {
  sourceIds: readonly string[];
  visibility: VisibilitySource;
  clock: Clock;
  sink: StartupWatchSink;
  timeoutMs?: number;
}

export interface StartupWatch {
  /** A sourcedata event with isSourceLoaded true. */
  sourceLoaded: (sourceId: string) => void;
  /** An error event naming a source. Returns true when it was a basemap source. */
  sourceError: (sourceId: string) => boolean;
  /** deck.gl's onAfterRender. */
  afterRender: () => void;
  /** A setProps call; stationsShown when the stations layer is visible and non-empty. */
  layersSubmitted: (stationsShown: boolean) => void;
  /** deck.gl's onError. Returns true when it failed the start-up (before the first render). */
  overlayError: (message: string) => boolean;
  dispose: () => void;
}

export function createStartupWatch(options: StartupWatchOptions): StartupWatch {
  const { sourceIds, visibility, clock, sink } = options;
  const basemap = new Set(sourceIds);
  const loaded = new Set<string>();
  const failed = new Set<string>();
  const slow = new Set<string>();
  const budgetMs = options.timeoutMs ?? MAP_STARTUP_TIMEOUT_MS;
  let remaining = budgetMs;
  let armedAt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let deadlinePassed = false;
  let rendered = false;
  let startupFailed = false;
  let stationsPending = false;
  let disposed = false;
  let stopVisibility: (() => void) | null = null;

  const pending = (): string[] => sourceIds.filter((id) => !loaded.has(id) && !failed.has(id));
  const report = (): void => {
    sink.basemapIssues([...failed].sort(), [...slow].sort());
  };

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function stopWatching(): void {
    clearTimer();
    stopVisibility?.();
    stopVisibility = null;
  }

  function settle(): void {
    if (deadlinePassed || (rendered && pending().length === 0)) {
      stopWatching();
    }
  }

  function fire(): void {
    timer = null;
    if (disposed || deadlinePassed) {
      return;
    }
    deadlinePassed = true;
    const late = pending();
    if (late.length > 0) {
      for (const id of late) {
        slow.add(id);
      }
      console.warn(`[map] basemap sources still loading after ${String(budgetMs / 1000)} s: ${late.join(", ")}`);
      report();
    }
    if (!rendered && !startupFailed) {
      startupFailed = true;
      sink.rendererFailed("timeout", "deck.gl did not render within the start-up deadline");
    }
    settle();
  }

  function arm(): void {
    if (timer === null && !deadlinePassed && !disposed) {
      armedAt = clock.now();
      timer = setTimeout(fire, Math.max(0, remaining));
    }
  }

  function onVisibilityChange(): void {
    if (disposed || deadlinePassed) {
      return;
    }
    if (visibility.isVisible()) {
      arm();
    } else if (timer !== null) {
      clearTimer();
      remaining -= clock.now() - armedAt;
    }
  }

  stopVisibility = visibility.onChange(onVisibilityChange);
  if (visibility.isVisible()) {
    arm();
  }

  return {
    sourceLoaded: (sourceId) => {
      if (disposed || !basemap.has(sourceId) || failed.has(sourceId)) {
        return;
      }
      loaded.add(sourceId);
      if (slow.delete(sourceId)) {
        report();
      }
      if (loaded.size === basemap.size && failed.size === 0) {
        markOnce(MARKS.basemapLoaded);
      }
      settle();
    },
    sourceError: (sourceId) => {
      if (!basemap.has(sourceId)) {
        return false;
      }
      if (disposed || failed.has(sourceId)) {
        return true;
      }
      failed.add(sourceId);
      loaded.delete(sourceId);
      slow.delete(sourceId);
      console.warn(`[map] basemap source "${sourceId}" could not be loaded`);
      report();
      settle();
      return true;
    },
    afterRender: () => {
      if (disposed) {
        return;
      }
      if (!rendered && !startupFailed) {
        rendered = true;
        sink.rendererReady();
      }
      if (stationsPending) {
        stationsPending = false;
        markOnce(MARKS.stationsRendered);
      }
      settle();
    },
    layersSubmitted: (stationsShown) => {
      if (!disposed && stationsShown) {
        stationsPending = true;
      }
    },
    overlayError: (message) => {
      if (disposed || rendered || startupFailed) {
        return false;
      }
      startupFailed = true;
      sink.rendererFailed("overlay", message);
      settle();
      return true;
    },
    dispose: () => {
      disposed = true;
      stopWatching();
    },
  };
}
