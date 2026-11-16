import type { BackendClient } from "../../lib/backend/client";
import { GifWriter } from "../forecastExport/GifWriter";
import { WebpWriter } from "../forecastExport/WebpWriter";
export type AnimationFormat = "gif" | "webp";
export interface AnimationFrame {
    image: Blob;
    label: string;
}
export async function encodeAnimation(frames: readonly AnimationFrame[], width: number, height: number, format: AnimationFormat, delay: number, client: BackendClient, signal: AbortSignal, progress: (done: number) => void, preview: HTMLCanvasElement, ramp?: Uint8Array): Promise<Blob> {
    signal.throwIfAborted();
    if (frames.length < 2)
        throw new Error("Select at least two animation frames.");
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (context === null)
        throw new Error("Animation export is unavailable.");
    const perFrame = Math.max(1, Math.min(4096, Math.floor(131072 / frames.length)));
    const sample = new Uint8Array(perFrame * frames.length * 4 + (ramp?.length ?? 0));
    if (format === "gif") {
        const thumb = document.createElement("canvas");
        thumb.width = 128;
        thumb.height = 72;
        const ctx = thumb.getContext("2d", { willReadFrequently: true });
        if (ctx === null)
            throw new Error("Animation color sampling is unavailable.");
        for (const [index, frame] of frames.entries()) {
            signal.throwIfAborted();
            const bitmap = await createImageBitmap(frame.image);
            try {
                ctx.drawImage(bitmap, 0, 0, 128, 72);
                const pixels = ctx.getImageData(0, 0, 128, 72).data;
                for (let i = 0; i < perFrame; i++) {
                    const offset = Math.floor(i * (pixels.length / 4) / perFrame) * 4;
                    sample.set(pixels.subarray(offset, offset + 4), (index * perFrame + i) * 4);
                }
            }
            finally {
                bitmap.close();
            }
        }
        if (ramp !== undefined)
            sample.set(ramp, perFrame * frames.length * 4);
        thumb.width = 0;
        thumb.height = 0;
    }
    const gif = format === "gif" ? new GifWriter(signal) : null;
    const webp = format === "webp" ? new WebpWriter(client, signal, width, height) : null;
    try {
        preview.width = width;
        preview.height = height;
        for (const [index, frame] of frames.entries()) {
            signal.throwIfAborted();
            const bitmap = await createImageBitmap(frame.image);
            try {
                context.drawImage(bitmap, 0, 0, width, height);
            }
            finally {
                bitmap.close();
            }
            if (gif !== null) {
                const rgba = context.getImageData(0, 0, width, height).data;
                await gif.write({ rgba: rgba.buffer, width, height, delay, ...(index === 0 ? { paletteSample: sample } : {}) });
            }
            else if (webp !== null)
                await webp.write(frame.image, delay);
            signal.throwIfAborted();
            preview.getContext("2d")?.drawImage(canvas, 0, 0);
            progress(index + 1);
        }
        if (gif !== null)
            return gif.finish();
        if (webp !== null)
            return webp.finish();
        throw new Error("The animation encoder is unavailable.");
    }
    finally {
        gif?.close();
        webp?.close();
        canvas.width = 0;
        canvas.height = 0;
    }
}
