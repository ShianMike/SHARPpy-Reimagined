/// <reference lib="webworker" />
import { createGifEncoder, type GifFrame, type GifReply } from "./gifEncoder";

const worker = self as unknown as DedicatedWorkerGlobalScope;
const encode = createGifEncoder();
worker.onmessage = ({ data }: MessageEvent<GifFrame>) => {
  void Promise.resolve().then(() => {
    const bytes = encode(data);
    worker.postMessage({ bytes } satisfies GifReply, [bytes.buffer]);
  }).catch((error: unknown) => {
    worker.postMessage({ error: error instanceof Error ? error.message : "GIF encoding failed." } satisfies GifReply);
  });
};
