import { applyPalette, GIFEncoder, quantize } from "gifenc";

export interface GifFrame {
  rgba: ArrayBuffer;
  width: number;
  height: number;
  delay: number;
  paletteSample?: Uint8Array;
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
      // A shared palette sampled from every captured hour keeps map colors stable while
      // retaining colors that do not appear in the first hour.
      palette = quantize(frame.paletteSample ?? rgba, 256);
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
