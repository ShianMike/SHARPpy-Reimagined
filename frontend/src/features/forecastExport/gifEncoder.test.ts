import { applyPalette, GIFEncoder, quantize } from "gifenc";
import { expect, it } from "vitest";
import { createGifEncoder } from "./gifEncoder";

interface DecodedGifFrame {
  left: number; top: number; width: number; height: number; delay: number; transparent: boolean;
  minCodeSize: number; maxCodeSize: number; rgba: Uint8Array;
}

function decodeGifAnimation(bytes: Uint8Array): { width: number; height: number; loop: number; frames: DecodedGifFrame[] } {
  const sig = Buffer.from(bytes.subarray(0, 6)).toString("ascii");
  if (sig !== "GIF89a") throw new Error(`Expected GIF89a header, got ${sig}`);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint16(6, true), height = view.getUint16(8, true), packed = bytes[10] ?? 0;
  if ((packed & 0x80) === 0) throw new Error("Expected global color table");
  const gctSize = 1 << ((packed & 7) + 1);
  let pos = 13;
  const gct: [number, number, number][] = [];
  for (let i = 0; i < gctSize; i++) gct.push([bytes[pos++] ?? 0, bytes[pos++] ?? 0, bytes[pos++] ?? 0]);

  let loop = -1, pendingDelay = 0, pendingTrans = false, pendingTransIndex = 0, pendingDispose = 0;
  const canvas = new Uint8Array(width * height * 4);
  const frames: DecodedGifFrame[] = [];
  const readSubBlocks = (): Uint8Array => {
    const chunks: Uint8Array[] = [];
    for (let len = bytes[pos++] ?? 0; len !== 0; len = bytes[pos++] ?? 0) {
      if (pos + len > bytes.length) throw new Error("Truncated GIF sub-block");
      chunks.push(bytes.subarray(pos, pos + len));
      pos += len;
    }
    return Buffer.concat(chunks);
  };

  while (pos < bytes.length) {
    const block = bytes[pos++];
    if (block === 0x3b) {
      if (pos !== bytes.length) throw new Error("Trailing bytes after GIF trailer");
      return { width, height, loop, frames };
    }
    if (block === 0x21) {
      const label = bytes[pos++];
      if (label === 0xf9) {
        if (bytes[pos++] !== 4) throw new Error("Invalid GCE length");
        const gcePacked = bytes[pos++] ?? 0;
        pendingDispose = (gcePacked >> 2) & 7;
        pendingTrans = (gcePacked & 1) !== 0;
        pendingDelay = view.getUint16(pos, true) * 10; pos += 2;
        pendingTransIndex = bytes[pos++] ?? 0;
        if (bytes[pos++] !== 0) throw new Error("Invalid GCE terminator");
      } else if (label === 0xff) {
        const sub = readSubBlocks();
        if (Buffer.from(sub.subarray(0, 11)).toString("ascii") === "NETSCAPE2.0") loop = (sub[12] ?? 0) | ((sub[13] ?? 0) << 8);
      } else readSubBlocks();
      continue;
    }
    if (block !== 0x2c) throw new Error(`Unexpected GIF block at ${String(pos - 1)}`);

    const left = view.getUint16(pos, true), top = view.getUint16(pos + 2, true);
    const fw = view.getUint16(pos + 4, true), fh = view.getUint16(pos + 6, true);
    const imgPacked = bytes[pos + 8] ?? 0; pos += 9;
    expect(imgPacked & 0x80).toBe(0);
    expect(fw).toBeGreaterThan(0); expect(fh).toBeGreaterThan(0);
    expect(left + fw).toBeLessThanOrEqual(width); expect(top + fh).toBeLessThanOrEqual(height);

    const minCodeSize = bytes[pos++] ?? 0;
    expect(minCodeSize).toBeGreaterThanOrEqual(2); expect(minCodeSize).toBeLessThanOrEqual(8);
    const data = readSubBlocks();
    expect(data.length).toBeGreaterThan(0);

    const clearCode = 1 << minCodeSize, eoiCode = clearCode + 1;
    let nextCode = eoiCode + 1, codeSize = minCodeSize + 1, maxCodeSize = codeSize, bitPos = 0;
    const dict: number[][] = [];
    const reset = (): void => {
      dict.length = 0;
      for (let i = 0; i < clearCode; i++) dict[i] = [i];
      dict[clearCode] = []; dict[eoiCode] = [];
      nextCode = eoiCode + 1; codeSize = minCodeSize + 1;
    };
    const nextBits = (): number => {
      let code = 0;
      for (let b = 0; b < codeSize; b++) {
        const byteIdx = bitPos >> 3;
        if (byteIdx >= data.length) throw new Error("LZW bitstream overrun");
        code |= (((data[byteIdx] ?? 0) >> (bitPos & 7)) & 1) << b;
        bitPos++;
      }
      return code;
    };

    reset();
    const out: number[] = [];
    let prevEntry: number[] | null = null;
    for (let code = nextBits(); code !== eoiCode; code = nextBits()) {
      if (code === clearCode) { reset(); prevEntry = null; continue; }
      let entry: number[];
      if (code < dict.length) {
        const existing = dict[code];
        if (existing === undefined) throw new Error(`Missing dictionary entry ${String(code)}`);
        entry = existing;
      } else if (code === nextCode && prevEntry !== null) entry = [...prevEntry, prevEntry[0] ?? 0];
      else throw new Error(`Invalid LZW code ${String(code)} (nextCode=${String(nextCode)})`);
      for (const value of entry) out.push(value);
      if (prevEntry !== null && nextCode < 4096) {
        dict[nextCode++] = [...prevEntry, entry[0] ?? 0];
        if (nextCode === (1 << codeSize) && codeSize < 12) maxCodeSize = Math.max(maxCodeSize, ++codeSize);
      }
      prevEntry = entry;
    }

    expect(out).toHaveLength(fw * fh);
    expect(pendingDispose).toBe(1);
    for (let y = 0; y < fh; y++) {
      for (let x = 0; x < fw; x++) {
        const colorIndex = out[y * fw + x] ?? 0;
        if (pendingTrans && colorIndex === pendingTransIndex) continue;
        const rgb = gct[colorIndex];
        if (rgb === undefined) throw new Error(`Color index ${String(colorIndex)} outside palette`);
        const dst = ((top + y) * width + (left + x)) * 4;
        canvas[dst] = rgb[0]; canvas[dst + 1] = rgb[1]; canvas[dst + 2] = rgb[2]; canvas[dst + 3] = 255;
      }
    }
    frames.push({ left, top, width: fw, height: fh, delay: pendingDelay, transparent: pendingTrans, minCodeSize, maxCodeSize, rgba: canvas.slice() });
  }
  throw new Error("Missing GIF trailer");
}

function makePatternFrame(width: number, height: number, patchX: number, patchY: number): Uint8Array {
  const rgba = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4, inPatch = x >= patchX && x < patchX + 12 && y >= patchY && y < patchY + 12;
      rgba[o] = inPatch ? 255 : (x < width / 2 ? 20 : 210);
      rgba[o + 1] = inPatch ? 60 : (y < height / 2 ? 40 : 190);
      rgba[o + 2] = inPatch ? 20 : 90; rgba[o + 3] = 255;
    }
  }
  return rgba;
}

it("writes one global color table, looping metadata and exactly the selected frames", () => {
  const encode = createGifEncoder();
  const bytes = Buffer.concat([
    encode({ rgba: new Uint8Array([255, 0, 0, 255, 0, 0, 255, 255]).buffer, width: 2, height: 1, delay: 750 }),
    encode({ rgba: new Uint8Array([0, 0, 255, 255, 255, 0, 0, 255]).buffer, width: 2, height: 1, delay: 500 }),
    new Uint8Array([0x3b]),
  ]);
  const { width, height, loop, frames } = decodeGifAnimation(bytes);
  expect([width, height, loop]).toEqual([2, 1, 0]);
  expect(frames.map(({ delay }) => delay)).toEqual([750, 500]);
});

it("uses the supplied whole-animation sample for the shared color table", () => {
  const encode = createGifEncoder();
  const first = new Uint8Array([0, 0, 255, 255]), allHours = new Uint8Array([0, 0, 255, 255, 255, 0, 0, 255]);
  const bytes = Buffer.from(encode({ rgba: first.buffer, width: 1, height: 1, delay: 750, paletteSample: allHours }));
  const count = 2 ** (((bytes[10] ?? 0) & 7) + 1);
  const colors = Array.from({ length: count }, (_, index) => [...bytes.subarray(13 + index * 3, 16 + index * 3)]);
  expect(colors.some(([red, , blue]) => (red ?? 0) > 200 && (blue ?? 255) < 30)).toBe(true);
  expect(colors.some(([red, , blue]) => (blue ?? 0) > 200 && (red ?? 255) < 30)).toBe(true);
});

it("crops later frames to their changed bounding box with transparency and shrinks delta GIFs", () => {
  const width = 128, height = 96;
  const f0 = makePatternFrame(width, height, 10, 10), f1 = makePatternFrame(width, height, 22, 16), f2 = makePatternFrame(width, height, 30, 20);
  const encode = createGifEncoder();
  const bytes = Buffer.concat([encode({ rgba: f0.slice().buffer, width, height, delay: 750 }),
    encode({ rgba: f1.slice().buffer, width, height, delay: 750 }), encode({ rgba: f2.slice().buffer, width, height, delay: 500 }), new Uint8Array([0x3b])]);

  const decoded = decodeGifAnimation(bytes);
  expect(decoded.loop).toBe(0);
  expect(decoded.frames).toHaveLength(3);
  expect(decoded.frames[0]).toMatchObject({ left: 0, top: 0, width, height, delay: 750, transparent: false });
  expect(decoded.frames[1]).toMatchObject({ left: 10, top: 10, width: 24, height: 18, delay: 750, transparent: true });
  expect(decoded.frames[2]).toMatchObject({ left: 22, top: 16, width: 20, height: 16, delay: 500, transparent: true });
  expect(decoded.frames[0]?.rgba).toEqual(f0); expect(decoded.frames[1]?.rgba).toEqual(f1); expect(decoded.frames[2]?.rgba).toEqual(f2);

  const naive = GIFEncoder({ auto: false }), pal = quantize(f0, 256);
  naive.writeHeader();
  for (const [i, frame] of [f0, f1, f2].entries()) {
    naive.writeFrame(applyPalette(frame, pal), width, height, { first: i === 0, ...(i === 0 ? { palette: pal } : {}), delay: 750, repeat: 0, dispose: 1 });
  }
  expect(bytes.length).toBeLessThan(naive.bytes().length + 1);
});

it("emits a 1x1 transparent frame for identical consecutive frames without losing timing", () => {
  const width = 40, height = 30, f0 = makePatternFrame(width, height, 8, 8), encode = createGifEncoder();
  const bytes = Buffer.concat([encode({ rgba: f0.slice().buffer, width, height, delay: 750 }), encode({ rgba: f0.slice().buffer, width, height, delay: 250 }), new Uint8Array([0x3b])]);
  const decoded = decodeGifAnimation(bytes);
  expect(decoded.frames).toHaveLength(2);
  expect(decoded.frames[1]).toMatchObject({ left: 0, top: 0, width: 1, height: 1, delay: 250, transparent: true });
  expect(decoded.frames[1]?.rgba).toEqual(f0);
});

it("keeps dark basemap texture and low-contrast changes across composited frames", () => {
  const width = 192, height = 96;
  const terrain = [32, 40, 48, 255], texture = [40, 48, 56, 255];
  const boundary = [112, 128, 144, 255], station = [232, 72, 32, 255];
  const first = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const textured = x === 0 ? y % 2 === 1 : (((x * 13) ^ (y * 7)) & 15) < 4;
      let color = textured ? texture : terrain;
      if (x === 76 + Math.floor(y / 8) && y % 5 < 3) color = boundary;
      if ((x - 108) ** 2 + (y - 38) ** 2 <= 4) color = station;
      if (x >= width - 12 && y < 80) {
        const band = Math.floor(y / 4);
        color = [80 + 8 * (band % 10), 112 + 8 * Math.floor(band / 10), 160, 255];
      }
      first.set(color, (y * width + x) * 4);
    }
  }

  const updated = first.slice();
  for (let y = 20; y < 26; y++) {
    for (let x = 28; x < 34; x++) {
      const offset = (y * width + x) * 4;
      updated.set(first[offset] === terrain[0] ? texture : terrain, offset);
    }
  }

  const paletteSample = new Uint8Array(first.length + updated.length);
  paletteSample.set(first);
  paletteSample.set(updated, first.length);
  const sourceFrames = [first, updated, updated, first];
  const encode = createGifEncoder();
  const chunks = sourceFrames.map((rgba, index) => encode({
    rgba: rgba.buffer, width, height, delay: 500, ...(index === 0 ? { paletteSample } : {}),
  }));
  const { frames } = decodeGifAnimation(Buffer.concat([...chunks, new Uint8Array([0x3b])]));

  expect(frames).toHaveLength(sourceFrames.length);
  const wrongPixels = frames.map(({ rgba }, index) => {
    const expected = sourceFrames[index];
    let count = 0;
    for (let offset = 0; offset < rgba.length; offset += 4) {
      if (rgba[offset] !== expected?.[offset] || rgba[offset + 1] !== expected?.[offset + 1]
          || rgba[offset + 2] !== expected?.[offset + 2] || rgba[offset + 3] !== expected?.[offset + 3]) count++;
    }
    return count;
  });
  expect(wrongPixels).toEqual([0, 0, 0, 0]);
  expect(frames[1]).toMatchObject({ left: 28, top: 20, width: 6, height: 6, transparent: true });
  expect(frames[2]).toMatchObject({ width: 1, height: 1, delay: 500, transparent: true });
  expect(frames[3]).toMatchObject({ left: 28, top: 20, width: 6, height: 6, transparent: true });
});

it("retains nearby dark shades when the global palette has room", () => {
  const width = 32, height = 2;
  const dark = [32, 40, 48, 255], lighter = [40, 44, 52, 255];
  const rgba = new Uint8Array(width * height * 4);
  for (let x = 0; x < width; x++) {
    rgba.set(x % 2 === 0 ? dark : lighter, x * 4);
    rgba.set([x * 8, 144, 184, 255], (width + x) * 4);
  }

  const encode = createGifEncoder();
  const bytes = Buffer.concat([encode({ rgba: rgba.buffer, width, height, delay: 500 }), new Uint8Array([0x3b])]);
  expect(decodeGifAnimation(bytes).frames[0]?.rgba).toEqual(rgba);
});

it("maps nearby source colors independently within the same RGB565 bin", () => {
  const dark = [31, 47, 63, 255], lighter = [40, 52, 64, 255];
  const paletteSample = new Uint8Array([...dark, ...lighter]);
  const first = new Uint8Array([32, 48, 64, 255, 39, 51, 64, 255]);
  const second = new Uint8Array([39, 51, 64, 255, 32, 48, 64, 255]);
  const encode = createGifEncoder();
  const bytes = Buffer.concat([
    encode({ rgba: first.buffer, width: 2, height: 1, delay: 500, paletteSample }),
    encode({ rgba: second.buffer, width: 2, height: 1, delay: 500 }),
    new Uint8Array([0x3b]),
  ]);
  const { frames } = decodeGifAnimation(bytes);
  expect(frames.map(({ rgba }) => rgba)).toEqual([
    new Uint8Array([...dark, ...lighter]),
    new Uint8Array([...lighter, ...dark]),
  ]);
});

it("decodes 12-bit LZW animations without trailing bytes after the trailer", () => {
  const width = 128, height = 96;
  const makeFrame = (seed: number): Uint8Array<ArrayBuffer> => {
    const rgba = new Uint8Array(width * height * 4);
    let random = seed;
    for (let offset = 0; offset < rgba.length; offset += 4) {
      random = (Math.imul(random, 1664525) + 1013904223) >>> 0;
      const color = random >>> 26;
      rgba.set([(color >>> 4) * 85, ((color >>> 2) & 3) * 85, (color & 3) * 85, 255], offset);
    }
    return rgba;
  };
  const sources = [makeFrame(0), makeFrame(1)], encode = createGifEncoder();
  const chunks = sources.map(({ buffer }) => encode({ rgba: buffer, width, height, delay: 500 }));
  const { frames } = decodeGifAnimation(Buffer.concat([...chunks, new Uint8Array([0x3b])]));
  expect(frames.map(({ maxCodeSize }) => maxCodeSize)).toEqual([12, 12]);
  expect(frames.map(({ transparent }) => transparent)).toEqual([false, true]);
  expect(frames.map(({ rgba }, index) => Buffer.from(rgba).equals(Buffer.from(sources[index] ?? [])))).toEqual([true, true]);
});
