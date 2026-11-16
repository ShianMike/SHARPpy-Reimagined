import type { GifFrame, GifReply } from "./gifEncoder";

/** One in-flight pixel buffer; only compressed chunks accumulate for the download. */
export class GifWriter {
  private readonly worker: Worker;
  private readonly chunks: Uint8Array<ArrayBuffer>[] = [];
  private size = 0;
  private failure: Error | null = null;
  private pending: { resolve: () => void; reject: (error: Error) => void } | null = null;
  private readonly abort = (): void => { this.close(); };
  constructor(private readonly signal: AbortSignal) {
    signal.throwIfAborted();
    this.worker = new Worker(new URL("./gif.worker.ts", import.meta.url), { type: "module" });
    signal.addEventListener("abort", this.abort, { once: true });
    const fail = (error: Error): void => { this.failure = error; this.close(); };
    this.worker.onmessage = ({ data }: MessageEvent<GifReply>) => {
      if ("error" in data) { fail(new Error(data.error)); return; }
      this.size += data.bytes.length;
      if (this.size > 128 * 1024 * 1024) { fail(new Error("The GIF exceeds 128 MiB. Choose fewer hours or a smaller size.")); return; }
      const pending = this.pending;
      this.pending = null;
      this.chunks.push(data.bytes);
      pending?.resolve();
    };
    this.worker.onerror = this.worker.onmessageerror = () => { fail(new Error("The GIF worker could not encode this frame. Try again with a smaller size.")); };
  }
  write(frame: GifFrame): Promise<void> {
    this.signal.throwIfAborted();
    if (this.failure !== null) return Promise.reject(this.failure);
    return new Promise((resolve, reject) => {
      this.pending = { resolve, reject };
      this.worker.postMessage(frame, [frame.rgba]);
    });
  }
  finish(): Blob { return new Blob([...this.chunks, new Uint8Array([0x3b])], { type: "image/gif" }); }
  close(): void {
    this.failure ??= new DOMException("GIF export cancelled.", "AbortError");
    this.signal.removeEventListener("abort", this.abort);
    this.worker.onmessage = null; this.worker.onerror = null; this.worker.onmessageerror = null;
    this.worker.terminate();
    this.pending?.reject(this.failure);
    this.pending = null;
    this.chunks.length = 0;
  }
}
