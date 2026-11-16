import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GifReply } from "./gifEncoder";
import { GifWriter } from "./GifWriter";

class TestWorker {
  static instances: TestWorker[] = [];
  onmessage: ((event: MessageEvent<GifReply>) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessageerror: (() => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { TestWorker.instances.push(this); }
}

beforeEach(() => { TestWorker.instances = []; vi.stubGlobal("Worker", TestWorker); });
afterEach(() => { vi.unstubAllGlobals(); });
const frame = () => ({ rgba: new Uint8Array(4).buffer, width: 1, height: 1, delay: 750 });

it("does not create a worker for an already cancelled export", () => {
  const controller = new AbortController(); controller.abort();
  expect(() => new GifWriter(controller.signal)).toThrow();
  expect(TestWorker.instances).toHaveLength(0);
});

it.each(["onerror", "onmessageerror"] as const)("remembers an early %s instead of leaving the first frame waiting", async (event) => {
  const writer = new GifWriter(new AbortController().signal), worker = TestWorker.instances[0];
  if (worker === undefined) throw new Error("Worker fixture missing");
  worker[event]?.();
  let failure: unknown;
  void writer.write(frame()).catch((error: unknown) => { failure = error; });
  try {
    await vi.waitFor(() => { expect(failure).toBeInstanceOf(Error); }, { timeout: 500 });
    expect((failure as Error).message).toContain("could not encode");
    expect(worker.postMessage).not.toHaveBeenCalled();
    expect(worker.terminate).toHaveBeenCalled();
  } finally { writer.close(); }
});

it("settles an active frame with the encoding error and rejects later writes", async () => {
  const writer = new GifWriter(new AbortController().signal), worker = TestWorker.instances[0];
  if (worker === undefined) throw new Error("Worker fixture missing");
  const job = writer.write(frame());
  const failed = expect(job).rejects.toThrow("Bad palette");
  worker.onmessage?.({ data: { error: "Bad palette" } } as MessageEvent<GifReply>);
  await failed;
  let failure: unknown;
  void writer.write(frame()).catch((error: unknown) => { failure = error; });
  try {
    await vi.waitFor(() => { expect(failure).toBeInstanceOf(Error); }, { timeout: 500 });
    expect((failure as Error).message).toBe("Bad palette");
    expect(worker.postMessage).toHaveBeenCalledOnce();
    expect(worker.terminate).toHaveBeenCalled();
  } finally { writer.close(); }
});

it("collects successful chunks and cancels an in-flight frame", async () => {
  const controller = new AbortController(), writer = new GifWriter(controller.signal), worker = TestWorker.instances[0];
  if (worker === undefined) throw new Error("Worker fixture missing");
  const first = writer.write(frame());
  worker.onmessage?.({ data: { bytes: new Uint8Array([71, 73, 70]) } } as MessageEvent<GifReply>);
  await first;
  expect(new Uint8Array(await writer.finish().arrayBuffer())).toEqual(new Uint8Array([71, 73, 70, 0x3b]));
  const next = writer.write(frame());
  const cancelled = expect(next).rejects.toMatchObject({ name: "AbortError" });
  controller.abort(); await cancelled;
  expect(worker.terminate).toHaveBeenCalledOnce();
});
