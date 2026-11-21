import type { BackendClient } from "../../lib/backend/client";
import { ContractError } from "../../lib/contracts/guards";

const LOSSLESS_CHUNK = 0x5650384c; // "VP8L" in file order.
const MAX_ANIMATION_BYTES = 128 * 1024 * 1024;

function chunk(name: string, data: Uint8Array): Uint8Array<ArrayBuffer> {
    const bytes = new Uint8Array(8 + data.length + (data.length % 2));
    bytes.set(new TextEncoder().encode(name));
    new DataView(bytes.buffer).setUint32(4, data.length, true);
    bytes.set(data, 8);
    return bytes;
}
function set24(data: Uint8Array, index: number, value: number): void {
    data[index] = value & 255;
    data[index + 1] = (value >>> 8) & 255;
    data[index + 2] = (value >>> 16) & 255;
}
/** Lossless native frames in the standard looping ANIM/ANMF WebP container. */
export class WebpWriter {
    private readonly frames: Blob[] = [];
    private size = 0;
    constructor(private readonly client: BackendClient, private readonly signal: AbortSignal, private readonly width: number, private readonly height: number) { }
    async write(png: Blob, delay: number): Promise<void> {
        this.signal.throwIfAborted();
        if (png.size > 6 * 1024 * 1024 - 16384)
            throw new Error("This lossless frame is too large. Choose a smaller export resolution.");
        const encoded = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                if (typeof reader.result !== "string")
                    reject(new Error("The frame could not be read as a PNG."));
                else
                    resolve(reader.result.split(",")[1] ?? "");
            };
            reader.onerror = () => { reject(new Error("The animation frame could not be read.")); };
            reader.readAsDataURL(png);
        });
        const frame = await this.client.encodeWebpFrame(encoded, this.signal);
        this.signal.throwIfAborted();
        if (frame.width !== this.width || frame.height !== this.height)
            throw new ContractError("WebP", "frame dimensions changed");
        const bytes = frame.bytes;
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        let lossless: Uint8Array<ArrayBuffer> | undefined;
        for (let offset = 12; offset + 8 <= bytes.length;) {
            const count = view.getUint32(offset + 4, true);
            const end = offset + 8 + count + (count % 2);
            if (end > bytes.length)
                throw new ContractError("WebP", "invalid frame chunk");
            if (view.getUint32(offset) === LOSSLESS_CHUNK) {
                if (lossless !== undefined)
                    throw new ContractError("WebP", "expected one lossless frame");
                lossless = bytes.subarray(offset, end);
            }
            offset = end;
        }
        if (lossless === undefined)
            throw new ContractError("WebP", "expected one lossless frame");
        const header = new Uint8Array(24);
        const nextSize = this.size + header.length + lossless.length;
        if (nextSize > MAX_ANIMATION_BYTES)
            throw new Error("The animation exceeds 128 MiB. Choose fewer hours or a smaller size.");
        header.set(new TextEncoder().encode("ANMF"));
        new DataView(header.buffer).setUint32(4, 16 + lossless.length, true);
        set24(header, 14, this.width - 1);
        set24(header, 17, this.height - 1);
        set24(header, 20, delay);
        header[23] = 2; // Each frame replaces the full canvas.
        this.frames.push(new Blob([header, lossless]));
        this.size = nextSize;
    }
    finish(): Blob {
        if (this.frames.length < 2)
            throw new Error("Select at least two animation frames.");
        const extended = new Uint8Array(10);
        extended[0] = 0x12;
        set24(extended, 4, this.width - 1);
        set24(extended, 7, this.height - 1);
        const anim = new Uint8Array(6); // Transparent background and infinite loop.
        const header = new Uint8Array(12);
        header.set(new TextEncoder().encode("RIFF"));
        header.set(new TextEncoder().encode("WEBP"), 8);
        const extendedChunk = chunk("VP8X", extended);
        const animationChunk = chunk("ANIM", anim);
        new DataView(header.buffer).setUint32(4, 4 + extendedChunk.length + animationChunk.length + this.size, true);
        return new Blob([header, extendedChunk, animationChunk, ...this.frames], { type: "image/webp" });
    }
    close(): void {
        this.frames.length = 0;
        this.size = 0;
    }
}
