// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { copyForecastGif } from "./tauriTransport";

afterEach(() => vi.unstubAllGlobals());

it("passes GIF bytes directly to Tauri without a JSON argument wrapper", async () => {
  const invoke = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  const bytes = new Uint8Array([71, 73, 70, 56, 57, 97]);
  await copyForecastGif(bytes);
  expect(invoke).toHaveBeenCalledOnce();
  expect(invoke).toHaveBeenCalledWith("copy_forecast_gif", bytes, undefined);
  expect(invoke.mock.calls[0]?.[1]).toBe(bytes);
});

it("propagates native clipboard errors", async () => {
  const error = new Error("clipboard unavailable");
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke: vi.fn().mockRejectedValue(error) });
  await expect(copyForecastGif(new Uint8Array([1]))).rejects.toBe(error);
});
