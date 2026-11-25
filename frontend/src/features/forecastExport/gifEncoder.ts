import { quantize } from "gifenc";
import { ByteStream, GifLzwEncoder, writeGifHeader } from "./gifLzw";

export interface GifFrame {
  rgba: ArrayBuffer;
  width: number;
  height: number;
  delay: number;
  paletteSample?: Uint8Array;
}

export type GifReply = { bytes: Uint8Array<ArrayBuffer> } | { error: string };

const MAX_OPAQUE_COLORS = 127;

function nearestPaletteIndex(
  r: number, g: number, b: number,
  paletteR: Int16Array, paletteG: Int16Array, paletteB: Int16Array,
): number {
  let best = 0, bestDist = Infinity;
  for (let i = 0; i < paletteR.length; i++) {
    const dr = (paletteR[i] ?? 0) - r, dg = (paletteG[i] ?? 0) - g, db = (paletteB[i] ?? 0) - b;
    const dist = dr * dr + dg * dg + db * db;
    if (dist < bestDist) { bestDist = dist; best = i; }
  }
  return best;
}

/** Quantize to a shared palette, then compress the indexed frames losslessly. */
export function createGifEncoder() {
  const out = new ByteStream(65_536);
  const lzw = new GifLzwEncoder();
  const rgbCache = new Int32Array(65_536).fill(-1);

  let screenWidth = 0, screenHeight = 0, minCodeSize = 2, transparentIndex = 0;
  let paletteR: Int16Array | undefined, paletteG: Int16Array | undefined, paletteB: Int16Array | undefined;
  let displayed: Uint8Array | undefined, indicesBuf: Uint8Array | undefined, unchangedBuf: Uint8Array | undefined;

  return (frame: GifFrame): Uint8Array<ArrayBuffer> => {
    const { width, height, delay } = frame;
    if (width <= 0 || height <= 0) throw new Error("GIF frames must have positive dimensions.");
    const rgba = new Uint8Array(frame.rgba);
    const pixelCount = width * height;
    if (rgba.length < pixelCount * 4) throw new Error("GIF frame buffer is smaller than its dimensions.");

    if (displayed === undefined) {
      screenWidth = width; screenHeight = height;
      const quantized = quantize(frame.paletteSample ?? rgba, MAX_OPAQUE_COLORS);
      const palette = quantized.length > 0 ? quantized : [[0, 0, 0]];
      transparentIndex = palette.length;
      const gctBits = Math.max(1, Math.ceil(Math.log2(palette.length + 1)));
      minCodeSize = Math.max(2, gctBits);

      paletteR = new Int16Array(palette.length);
      paletteG = new Int16Array(palette.length);
      paletteB = new Int16Array(palette.length);
      for (let i = 0; i < palette.length; i++) {
        paletteR[i] = palette[i]?.[0] ?? 0; paletteG[i] = palette[i]?.[1] ?? 0; paletteB[i] = palette[i]?.[2] ?? 0;
      }
      indicesBuf = new Uint8Array(pixelCount);
      unchangedBuf = new Uint8Array(pixelCount);
      writeGifHeader(out, width, height, palette, gctBits);
    } else if (width !== screenWidth || height !== screenHeight) {
      throw new Error("All GIF frames must share the same dimensions.");
    }

    if (!paletteR || !paletteG || !paletteB || !indicesBuf || !unchangedBuf) {
      throw new Error("The GIF palette could not be prepared.");
    }

    const indices = indicesBuf;

    for (let p = 0; p < pixelCount; p++) {
      const o = p * 4, r = rgba[o] ?? 0, g = rgba[o + 1] ?? 0, b = rgba[o + 2] ?? 0;
      const rgb = (r << 16) | (g << 8) | b;
      const slot = Math.imul(rgb, 0x9e3779b1) >>> 16;
      // Store the full RGB key with the index so hash collisions leave color matching deterministic.
      const cached = rgbCache[slot] ?? -1;
      if (cached !== -1 && (cached >>> 8) === rgb) {
        indices[p] = cached & 0xff;
      } else {
        const index = nearestPaletteIndex(r, g, b, paletteR, paletteG, paletteB);
        rgbCache[slot] = (rgb << 8) | index;
        indices[p] = index;
      }
    }

    const sharedFrame = { transparentIndex, minCodeSize, delay };

    if (displayed === undefined) {
      lzw.writeFrame(out, {
        ...sharedFrame, left: 0, top: 0, width, height, transparent: false, indices, unchanged: null,
      });
      displayed = new Uint8Array(indices);
    } else {
      const unchanged = unchangedBuf;
      let minX = width, minY = height, maxX = -1, maxY = -1;

      for (let y = 0; y < height; y++) {
        const row = y * width;
        for (let x = 0; x < width; x++) {
          const p = row + x;
          if (indices[p] === displayed[p]) {
            unchanged[p] = 1;
            continue;
          }
          unchanged[p] = 0;
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }

      if (maxX < minX) {
        lzw.writeFrame(out, {
          ...sharedFrame, left: 0, top: 0, width: 1, height: 1, transparent: true,
          indices: new Uint8Array([transparentIndex]), unchanged: new Uint8Array([1]),
        });
      } else {
        const subW = maxX - minX + 1, subH = maxY - minY + 1;
        const fullRect = minX === 0 && minY === 0 && subW === width && subH === height;
        let subIdx = indices, subUnch = unchanged;

        if (!fullRect) {
          subIdx = new Uint8Array(subW * subH);
          subUnch = new Uint8Array(subW * subH);
          for (let sy = 0; sy < subH; sy++) {
            const srcStart = (minY + sy) * width + minX, dstStart = sy * subW;
            subIdx.set(indices.subarray(srcStart, srcStart + subW), dstStart);
            subUnch.set(unchanged.subarray(srcStart, srcStart + subW), dstStart);
          }
        }

        lzw.writeFrame(out, {
          ...sharedFrame, left: minX, top: minY, width: subW, height: subH, transparent: true,
          indices: subIdx, unchanged: subUnch,
        });

        if (fullRect) displayed.set(subIdx);
        else for (let sy = 0; sy < subH; sy++) displayed.set(subIdx.subarray(sy * subW, (sy + 1) * subW), (minY + sy) * width + minX);
      }
    }

    const bytes = out.bytes();
    out.reset();
    return bytes;
  };
}
