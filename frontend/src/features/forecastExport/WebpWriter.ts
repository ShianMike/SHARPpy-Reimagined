import type { BackendClient } from "../../lib/backend/client";
import { ContractError } from "../../lib/contracts/guards";
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
    private readonly frames: Uint8Array<ArrayBuffer>[] = [];
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
        const chunks: Uint8Array<ArrayBuffer>[] = [];
        for (let offset = 12; offset + 8 <= frame.bytes.length;) {
            const name = new TextDecoder().decode(frame.bytes.subarray(offset, offset + 4));
            const count = new DataView(frame.bytes.buffer).getUint32(offset + 4, true);
            const end = offset + 8 + count + (count % 2);
            if (end > frame.bytes.length)
                throw new ContractError("WebP", "invalid frame chunk");
            if (name === "VP8L")
                chunks.push(frame.bytes.slice(offset, end));
            offset = end;
        }
        const lossless = chunks[0];
        if (chunks.length !== 1 || lossless === undefined)
            throw new ContractError("WebP", "expected one lossless frame");
        const payload = new Uint8Array(16 + lossless.length);
        set24(payload, 6, this.width - 1);
        set24(payload, 9, this.height - 1);
        set24(payload, 12, delay);
        payload[15] = 2; // Every frame replaces the full canvas; never blend transitions.
        payload.set(lossless, 16);
        const next = chunk("ANMF", payload);
        this.size += next.length;
        if (this.size > 128 * 1024 * 1024)
            throw new Error("The animation exceeds 128 MiB. Choose fewer hours or a smaller size.");
        this.frames.push(next);
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
        const parts = [chunk("VP8X", extended), chunk("ANIM", anim), ...this.frames];
        new DataView(header.buffer).setUint32(4, 4 + parts.reduce((sum, item) => sum + item.length, 0), true);
        return new Blob([header, ...parts], { type: "image/webp" });
    }
    close(): void { this.frames.length = 0; }
}
