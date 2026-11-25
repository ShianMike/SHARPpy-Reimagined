// Types for the fixture hook installed by the opt-in Playwright spec.
// Production builds fold away the references in ForecastExportDialog.
// Keep the fixture contract independent of the application modules so the
// Node-side Playwright project can type-check it without importing React code.
interface GifBenchmarkProgress { phase: "loading" | "capturing" | "encoding"; done: number; total: number; hour: number }
interface GifBenchmarkOptions {
  context: { model: { key: string } }; product: { key: string }; run: string;
  hours: readonly number[]; width: number; height: number; delay: number;
}

export interface GifBenchmarkEvent extends GifBenchmarkProgress { atMs: number }
export interface GifBenchmarkCapture {
  startedAtMs: number;
  finishedAtMs: number | null;
  options: Pick<GifBenchmarkOptions, "hours" | "width" | "height" | "delay" | "run"> & { model: string; product: string };
  events: GifBenchmarkEvent[];
  error: string | null;
}

export interface GifBenchmarkHook {
  start(options: GifBenchmarkOptions): void;
  progress(value: GifBenchmarkProgress): void;
  finish(blob: Blob): void;
  fail(error: unknown): void;
  capture: GifBenchmarkCapture | null;
  blob: Blob | null;
}

declare global {
  interface Window {
    __SHARPPY_GIF_BENCHMARK__?: GifBenchmarkHook;
  }
}

// The script is injected by Playwright using page.addInitScript; this module supplies types only.
