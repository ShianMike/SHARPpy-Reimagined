import { expect, it } from "vitest";
import { createGifEncoder } from "./gifEncoder";

it("writes one global color table, looping metadata and exactly the selected frames", () => {
  const encode = createGifEncoder();
  const chunks = [encode({ rgba: new Uint8Array([255, 0, 0, 255, 0, 0, 255, 255]).buffer, width: 2, height: 1, delay: 750 }),
    encode({ rgba: new Uint8Array([0, 0, 255, 255, 255, 0, 0, 255]).buffer, width: 2, height: 1, delay: 500 }), new Uint8Array([0x3b])];
  const bytes = Buffer.concat(chunks);
  expect(bytes.subarray(0, 6).toString()).toBe("GIF89a");
  expect([bytes.readUInt16LE(6), bytes.readUInt16LE(8)]).toEqual([2, 1]);
  const loop = bytes.indexOf("NETSCAPE2.0");
  expect(loop).toBeGreaterThan(0);
  expect(bytes.readUInt16LE(loop + 13)).toBe(0);
  let at = 13 + 3 * 2 ** (((bytes[10] ?? 0) & 7) + 1), frames = 0;
  const delays: number[] = [];
  const skipBlocks = () => { for (let size = bytes[at++]; size !== 0; size = bytes[at++]) at += size ?? 0; };
  while (bytes[at] !== 0x3b) {
    const block = bytes[at++];
    if (block === 0x21) {
      const type = bytes[at++];
      if (type === 0xf9) delays.push(bytes.readUInt16LE(at + 2) * 10);
      skipBlocks();
    } else if (block === 0x2c) {
      expect((bytes[(at += 8)] ?? 0) & 0x80).toBe(0); // No per-frame palette flicker.
      at += 2; skipBlocks(); frames++;
    } else throw new Error(`Unexpected GIF block at ${String(at - 1)}`);
  }
  expect(frames).toBe(2);
  expect(delays).toEqual([750, 500]);
  expect(at).toBe(bytes.length - 1);
});

it("uses the supplied whole-animation sample for the shared color table", () => {
  const encode = createGifEncoder();
  const first = new Uint8Array([0, 0, 255, 255]);
  const allHours = new Uint8Array([0, 0, 255, 255, 255, 0, 0, 255]);
  const bytes = Buffer.from(encode({ rgba: first.buffer, width: 1, height: 1, delay: 750, paletteSample: allHours }));
  const count = 2 ** (((bytes[10] ?? 0) & 7) + 1);
  const colors = Array.from({ length: count }, (_, index) => [...bytes.subarray(13 + index * 3, 16 + index * 3)]);
  expect(colors.some(([red, , blue]) => (red ?? 0) > 200 && (blue ?? 255) < 30)).toBe(true);
  expect(colors.some(([red, , blue]) => (blue ?? 0) > 200 && (red ?? 255) < 30)).toBe(true);
});
