import type { GifFrame, GifReply } from "./gifEncoder";

/** One in-flight pixel buffer; only compressed chunks accumulate for the download. */
export class GifWriter {
  private readonly worker = new Worker(new URL("./gif.worker.ts", import.meta.url), { type: "module" });
  private readonly chunks: Uint8Array<ArrayBuffer>[] = [];
  private size = 0;
  private pending: { resolve: () => void; reject: (error: Error) => void } | null = null;
  private readonly abort = (): void => { this.close(); };
  constructor(private readonly signal: AbortSignal) {
    signal.throwIfAborted();
    signal.addEventListener("abort", this.abort, { once: true });
    this.worker.onmessage = ({ data }: MessageEvent<GifReply>) => {
      const pending = this.pending;
      this.pending = null;
      if ("error" in data) { pending?.reject(new Error(data.error)); return; }
      this.size += data.bytes.length;
      if (this.size > 128 * 1024 * 1024) { pending?.reject(new Error("The GIF exceeds 128 MiB. Choose fewer hours or a smaller size.")); return; }
      this.chunks.push(data.bytes);
      pending?.resolve();
    };
    const fail = (): void => { this.pending?.reject(new Error("The GIF worker could not encode this frame. Try again with a smaller size.")); this.pending = null; };
    this.worker.onerror = fail;
    this.worker.onmessageerror = fail;
  }
  write(frame: GifFrame): Promise<void> {
    this.signal.throwIfAborted();
    return new Promise((resolve, reject) => {
      this.pending = { resolve, reject };
      this.worker.postMessage(frame, [frame.rgba]);
    });
  }
  finish(): Blob { return new Blob([...this.chunks, new Uint8Array([0x3b])], { type: "image/gif" }); }
  close(): void {
    this.signal.removeEventListener("abort", this.abort);
    this.worker.terminate();
    this.pending?.reject(new DOMException("GIF export cancelled.", "AbortError"));
    this.pending = null;
    this.chunks.length = 0;
  }
}
