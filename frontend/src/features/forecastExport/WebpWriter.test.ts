// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { WebpWriter } from "./WebpWriter";

function bytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => { resolve(new Uint8Array(reader.result as ArrayBuffer)); };
    reader.onerror = () => { reject(new Error("Read failed")); };
    reader.readAsArrayBuffer(blob);
  });
}
it("writes discrete full-canvas lossless frames, their durations and infinite looping", async () => {
  const client = new BackendClient(createTransport());
  const native = new Uint8Array(24);
  native.set(new TextEncoder().encode("RIFF")); new DataView(native.buffer).setUint32(4, 16, true);
  native.set(new TextEncoder().encode("WEBPVP8L"), 8);
  new DataView(native.buffer).setUint32(16, 3, true); native.set([0x2f, 1, 2], 20);
  vi.spyOn(client, "encodeWebpFrame").mockResolvedValue({ width: 640, height: 480, bytes: native });
  const writer = new WebpWriter(client, new AbortController().signal, 640, 480);
  await writer.write(new Blob(["png"]), 750); await writer.write(new Blob(["png"]), 1000);
  const blob = writer.finish(), data = await bytes(blob), view = new DataView(data.buffer);
  expect(blob.type).toBe("image/webp");
  expect(view.getUint32(4, true) + 8).toBe(data.length);
  expect(new TextDecoder().decode(data.slice(12, 16))).toBe("VP8X");
  expect(data[20]).toBe(0x12);
  const chunks: { type: string; data: Uint8Array }[] = [];
  for (let offset = 12; offset + 8 <= data.length;) {
    const count = view.getUint32(offset + 4, true);
    chunks.push({ type: new TextDecoder().decode(data.slice(offset, offset + 4)), data: data.slice(offset + 8, offset + 8 + count) });
    offset += 8 + count + count % 2;
  }
  expect(chunks.map((chunk) => chunk.type)).toEqual(["VP8X", "ANIM", "ANMF", "ANMF"]);
  expect(chunks[1]?.data).toEqual(new Uint8Array(6));
  for (const [index, chunk] of chunks.slice(2).entries()) {
    const header = new DataView(chunk.data.buffer);
    expect(chunk.data[15]).toBe(2);
    expect(header.getUint16(6, true)).toBe(639);
    expect(header.getUint16(9, true)).toBe(479);
    expect(header.getUint16(12, true)).toBe(index === 0 ? 750 : 1000);
    expect(new TextDecoder().decode(chunk.data.slice(16, 20))).toBe("VP8L");
  }
  writer.close();
  expect(() => writer.finish()).toThrow("at least two");
});
it("rejects changed dimensions and cancellation before calling the backend", async () => {
  const client = new BackendClient(createTransport()), controller = new AbortController();
  const encode = vi.spyOn(client, "encodeWebpFrame");
  controller.abort();
  await expect(new WebpWriter(client, controller.signal, 640, 480).write(new Blob(["png"]), 750)).rejects.toMatchObject({ name: "AbortError" });
  expect(encode).not.toHaveBeenCalled();
  encode.mockResolvedValue({ width: 1, height: 1, bytes: new Uint8Array() });
  await expect(new WebpWriter(client, new AbortController().signal, 640, 480).write(new Blob(["png"]), 750)).rejects.toThrow("dimensions changed");
});
