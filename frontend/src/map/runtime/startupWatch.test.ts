import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { MARKS } from "../../lib/metrics";
import { createStartupWatch, MAP_STARTUP_TIMEOUT_MS, type StartupWatchSink, type VisibilitySource } from "./startupWatch";

const SOURCES = ["states", "countries", "lakes", "coastline"];

/** A visibility source the test drives. */
function scriptedVisibility(initial: boolean): VisibilitySource & { set(visible: boolean): void; listeners: number } {
  let visible = initial;
  const listeners = new Set<() => void>();
  return {
    isVisible: () => visible,
    onChange: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set(next) {
      visible = next;
      for (const listener of [...listeners]) {
        listener();
      }
    },
    get listeners() {
      return listeners.size;
    },
  };
}

function recordingSink(): StartupWatchSink & {
  issues: { failed: readonly string[]; slow: readonly string[] }[];
  ready: number;
  failures: { kind: string; message: string }[];
} {
  const sink = {
    issues: [] as { failed: readonly string[]; slow: readonly string[] }[],
    ready: 0,
    failures: [] as { kind: string; message: string }[],
    basemapIssues: (failed: readonly string[], slow: readonly string[]) => {
      sink.issues.push({ failed, slow });
    },
    rendererReady: () => {
      sink.ready += 1;
    },
    rendererFailed: (kind: string, message: string) => {
      sink.failures.push({ kind, message });
    },
  };
  return sink;
}

function setup(visible = true) {
  const visibility = scriptedVisibility(visible);
  const sink = recordingSink();
  const watch = createStartupWatch({ sourceIds: SOURCES, visibility, clock: { now: () => Date.now() }, sink });
  return { visibility, sink, watch };
}

const markCount = (name: string): number => performance.getEntriesByName(name, "mark").length;

let warn: MockInstance<typeof console.warn>;

beforeEach(() => {
  // performance stays real: the watch sets User Timing marks.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  performance.clearMarks();
  warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("startup watch: basemap", () => {
  it("sets sharppy:basemap-loaded once when all four sources load", () => {
    const { watch, sink } = setup();
    for (const id of SOURCES) {
      watch.sourceLoaded(id);
    }
    watch.sourceLoaded("states");
    expect(markCount(MARKS.basemapLoaded)).toBe(1);
    expect(sink.issues).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
  });

  it("ignores sources that are not basemap sources", () => {
    const { watch, sink } = setup();
    watch.sourceLoaded("other");
    expect(watch.sourceError("other")).toBe(false);
    expect(sink.issues).toEqual([]);
  });

  it("reports a source still pending at the deadline as slow, and withdraws it when it loads", () => {
    const { watch, sink } = setup();
    watch.afterRender();
    for (const id of ["states", "countries", "lakes"]) {
      watch.sourceLoaded(id);
    }
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS);
    expect(sink.issues).toEqual([{ failed: [], slow: ["coastline"] }]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("coastline");
    expect(markCount(MARKS.basemapLoaded)).toBe(0);
    vi.advanceTimersByTime(20_000 - MAP_STARTUP_TIMEOUT_MS);
    watch.sourceLoaded("coastline");
    expect(sink.issues.at(-1)).toEqual({ failed: [], slow: [] });
    expect(markCount(MARKS.basemapLoaded)).toBe(1);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(sink.failures).toEqual([]);
  });

  it("keeps a failed source failed, even when MapLibre later reports it loaded", () => {
    const { watch, sink } = setup();
    expect(watch.sourceError("lakes")).toBe(true);
    expect(sink.issues).toEqual([{ failed: ["lakes"], slow: [] }]);
    for (const id of SOURCES) {
      watch.sourceLoaded(id);
    }
    expect(sink.issues).toEqual([{ failed: ["lakes"], slow: [] }]);
    expect(markCount(MARKS.basemapLoaded)).toBe(0);
  });

  it("warns once per failed source and once for the deadline", () => {
    const { watch, sink } = setup();
    watch.afterRender();
    watch.sourceError("states");
    watch.sourceError("states");
    watch.sourceError("countries");
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS);
    // states and countries failed; lakes and coastline were still pending.
    expect(warn).toHaveBeenCalledTimes(3);
    expect(sink.issues.at(-1)).toEqual({ failed: ["countries", "states"], slow: ["coastline", "lakes"] });
    // A slow source that then fails moves to the failed set.
    watch.sourceError("lakes");
    expect(sink.issues.at(-1)).toEqual({ failed: ["countries", "lakes", "states"], slow: ["coastline"] });
    expect(warn).toHaveBeenCalledTimes(4);
  });
});

describe("startup watch: visible-time deadline", () => {
  it("does not run while the page is hidden from construction", () => {
    const { watch, sink, visibility } = setup(false);
    vi.advanceTimersByTime(60_000);
    expect(sink.failures).toEqual([]);
    expect(sink.issues).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
    visibility.set(true);
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS - 1);
    expect(sink.failures).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(sink.failures.map((f) => f.kind)).toEqual(["timeout"]);
    expect(sink.issues).toEqual([{ failed: [], slow: SOURCES.slice().sort() }]);
    watch.dispose();
  });

  it("pauses while hidden and resumes with the remaining budget", () => {
    const { sink, visibility } = setup(true);
    vi.advanceTimersByTime(10_000);
    visibility.set(false);
    vi.advanceTimersByTime(30_000);
    expect(sink.failures).toEqual([]);
    visibility.set(true);
    vi.advanceTimersByTime(4_999);
    expect(sink.failures).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(sink.failures.map((f) => f.kind)).toEqual(["timeout"]);
  });

  it("fires at most once", () => {
    const { sink, visibility } = setup(true);
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS);
    visibility.set(false);
    visibility.set(true);
    vi.advanceTimersByTime(60_000);
    expect(sink.failures).toHaveLength(1);
  });
});

describe("startup watch: renderer", () => {
  it("sets the renderer ready on the first deck render, with or without stations", () => {
    const { watch, sink } = setup();
    watch.afterRender();
    watch.afterRender();
    expect(sink.ready).toBe(1);
    expect(markCount(MARKS.stationsRendered)).toBe(0);
  });

  it("fails with kind overlay on a deck error before the first render", () => {
    const { watch, sink } = setup();
    expect(watch.overlayError("shader failed")).toBe(true);
    expect(sink.failures).toEqual([{ kind: "overlay", message: "shader failed" }]);
    expect(watch.overlayError("shader failed")).toBe(true);
    expect(sink.failures).toHaveLength(1);
    watch.afterRender();
    expect(sink.ready).toBe(1);
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS);
    expect(sink.failures).toHaveLength(1);
  });

  it("reports a deck failure after the first render and recovers on a successful render", () => {
    const { watch, sink } = setup();
    watch.afterRender();
    expect(watch.overlayError("later")).toBe(true);
    expect(sink.failures).toEqual([{ kind: "overlay", message: "later" }]);
    watch.afterRender();
    expect(sink.ready).toBe(2);
  });

  it("fails with kind timeout when nothing renders by the deadline", () => {
    const { sink } = setup();
    vi.advanceTimersByTime(MAP_STARTUP_TIMEOUT_MS);
    expect(sink.failures.map((f) => f.kind)).toEqual(["timeout"]);
    expect(sink.ready).toBe(0);
  });

  it("sets sharppy:stations-rendered only after a visible, non-empty station layer was submitted", () => {
    const { watch } = setup();
    watch.layersSubmitted(false);
    watch.afterRender();
    expect(markCount(MARKS.stationsRendered)).toBe(0);
    watch.layersSubmitted(true);
    expect(markCount(MARKS.stationsRendered)).toBe(0);
    watch.afterRender();
    watch.layersSubmitted(true);
    watch.afterRender();
    expect(markCount(MARKS.stationsRendered)).toBe(1);
  });
});

describe("startup watch: clean-up", () => {
  it("clears the timer and the visibility listener once ready with no source pending", () => {
    const { watch, visibility } = setup();
    expect(visibility.listeners).toBe(1);
    expect(vi.getTimerCount()).toBe(1);
    for (const id of SOURCES) {
      watch.sourceLoaded(id);
    }
    expect(vi.getTimerCount()).toBe(1);
    watch.afterRender();
    expect(vi.getTimerCount()).toBe(0);
    expect(visibility.listeners).toBe(0);
  });

  it("clears the timer and the visibility listener on dispose, and ignores later events", () => {
    const { watch, sink, visibility } = setup();
    watch.dispose();
    expect(vi.getTimerCount()).toBe(0);
    expect(visibility.listeners).toBe(0);
    watch.sourceError("states");
    watch.afterRender();
    vi.advanceTimersByTime(60_000);
    expect(sink.issues).toEqual([]);
    expect(sink.ready).toBe(0);
    expect(sink.failures).toEqual([]);
  });
});
