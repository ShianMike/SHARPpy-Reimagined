// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { copyForecastGif } from "./forecastClipboard";

const native = vi.hoisted(() => vi.fn());
vi.mock("../../lib/backend/tauriTransport", () => ({ copyForecastGif: native }));

afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); });

it("passes the encoded GIF bytes to the native clipboard command", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  native.mockResolvedValue(undefined);
  const bytes = new Uint8Array([71, 73, 70, 56, 57, 97]).buffer;
  const gif = { arrayBuffer: () => Promise.resolve(bytes) } as Blob;
  await copyForecastGif(gif);
  expect(native).toHaveBeenCalledWith(new Uint8Array([71, 73, 70, 56, 57, 97]));
});

it("rejects an oversized GIF before reading it into memory", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  const arrayBuffer = vi.fn();
  const gif = { size: 16 * 1024 * 1024 + 1, arrayBuffer } as unknown as Blob;
  await expect(copyForecastGif(gif)).rejects.toThrow("too large to copy safely");
  expect(arrayBuffer).not.toHaveBeenCalled();
  expect(native).not.toHaveBeenCalled();
});

it("propagates native clipboard errors without substituting a still image", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  native.mockRejectedValue(new Error("clipboard unavailable"));
  const gif = { size: 6, arrayBuffer: () => Promise.resolve(new Uint8Array([71, 73, 70, 56, 57, 97]).buffer) } as Blob;
  await expect(copyForecastGif(gif)).rejects.toThrow("clipboard unavailable");
});

it("writes the actual GIF MIME and bytes when browser clipboard supports it", async () => {
  class TestClipboardItem {
    static supports(type: string) { return type === "image/gif"; }
    constructor(readonly data: Record<string, Blob>) {}
  }
  const write = vi.fn<(items: TestClipboardItem[]) => Promise<void>>().mockResolvedValue(undefined);
  vi.stubGlobal("ClipboardItem", TestClipboardItem);
  vi.stubGlobal("navigator", { clipboard: { write } });
  const gif = new Blob(["GIF89a"], { type: "image/gif" });
  await copyForecastGif(gif);
  expect(write).toHaveBeenCalledOnce();
  expect(write.mock.calls[0]?.[0][0]?.data).toEqual({ "image/gif": gif });
});

it("reports unsupported or rejected GIF clipboard writes without substituting a PNG", async () => {
  class UnsupportedClipboardItem {
    static supports() { return false; }
    constructor(readonly data: Record<string, Blob>) {}
  }
  const write = vi.fn().mockRejectedValue(new Error("denied"));
  vi.stubGlobal("ClipboardItem", UnsupportedClipboardItem);
  vi.stubGlobal("navigator", { clipboard: { write } });
  const gif = new Blob(["GIF89a"], { type: "image/gif" });
  await expect(copyForecastGif(gif)).rejects.toThrow("GIF clipboard unavailable");
  expect(write).not.toHaveBeenCalled();
  vi.stubGlobal("ClipboardItem", class {
    static supports() { return true; }
    constructor(readonly data: Record<string, Blob>) {}
  });
  await expect(copyForecastGif(gif)).rejects.toThrow("denied");
  expect(write).toHaveBeenCalledOnce();
});

it("reports a missing browser clipboard API", async () => {
  vi.stubGlobal("ClipboardItem", class { constructor(readonly data: Record<string, Blob>) {} });
  vi.stubGlobal("navigator", {});
  await expect(copyForecastGif(new Blob(["GIF89a"], { type: "image/gif" }))).rejects.toThrow("GIF clipboard unavailable");
});
