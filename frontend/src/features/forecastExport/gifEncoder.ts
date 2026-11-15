import { applyPalette, GIFEncoder, quantize } from "gifenc";

export interface GifFrame {
  rgba: ArrayBuffer;
  width: number;
  height: number;
  delay: number;
  colors?: Uint8Array;
}

export type GifReply = { bytes: Uint8Array<ArrayBuffer> } | { error: string };

/** A single global palette prevents stationary map colors from shimmering between frames. */
export function createGifEncoder() {
  const encoder = GIFEncoder({ auto: false });
  let palette: number[][] | undefined;
  return (frame: GifFrame): Uint8Array<ArrayBuffer> => {
    const rgba = new Uint8Array(frame.rgba);
    const first = palette === undefined;
    if (first) {
      const count = Math.min(65_536, rgba.length / 4);
      const colors = frame.colors;
      const sample = new Uint8Array(count * (colors === undefined ? 4 : 8));
      for (let i = 0; i < count; i++) {
        const at = Math.floor(i * (rgba.length / 4) / count) * 4;
        sample.set(rgba.subarray(at, at + 4), i * 4);
        // Give the complete scientific ramp weight even if some values are absent in the first hour.
        if (colors !== undefined) sample.set(colors.subarray(i * 4 % colors.length, i * 4 % colors.length + 4), (count + i) * 4);
      }
      palette = quantize(sample, 256);
      encoder.writeHeader();
    }
    if (palette === undefined) throw new Error("The GIF palette could not be prepared.");
    encoder.writeFrame(applyPalette(rgba, palette), frame.width, frame.height, {
      first, ...(first ? { palette } : {}), delay: frame.delay, repeat: 0, dispose: 1,
    });
    const bytes = encoder.bytes();
    encoder.reset();
    return bytes;
  };
}
