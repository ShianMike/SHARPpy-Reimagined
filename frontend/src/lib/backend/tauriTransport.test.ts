// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { copyForecastGif, getDesktopPlatform, openUpdateReleases, starGitHubRepository } from "./tauriTransport";

afterEach(() => vi.unstubAllGlobals());

it("uses separate local IPC actions for platform detection and opening releases", async () => {
  const invoke = vi.fn().mockResolvedValueOnce("macos").mockResolvedValueOnce(null).mockResolvedValueOnce("freebsd");
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  await expect(getDesktopPlatform()).resolves.toBe("macos");
  await openUpdateReleases();
  await expect(getDesktopPlatform()).resolves.toBe("unknown");
  expect(invoke).toHaveBeenNthCalledWith(1, "check_for_updates", { action: "getPlatform" }, undefined);
  expect(invoke).toHaveBeenNthCalledWith(2, "check_for_updates", { action: "openReleases" }, undefined);
});

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

it("invokes star_github_repository and validates its typed outcome", async () => {
  const invoke = vi.fn()
    .mockResolvedValueOnce({ status: "starred", url: "https://github.com/ShianMike/SHARPpy-Reimagined" })
    .mockResolvedValueOnce({ status: "openedBrowser", url: "https://github.com/ShianMike/SHARPpy-Reimagined" })
    .mockResolvedValueOnce({ status: "bad", url: 123 });
  vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  await expect(starGitHubRepository()).resolves.toEqual({
    status: "starred",
    url: "https://github.com/ShianMike/SHARPpy-Reimagined",
  });
  await expect(starGitHubRepository()).resolves.toEqual({
    status: "openedBrowser",
    url: "https://github.com/ShianMike/SHARPpy-Reimagined",
  });
  await expect(starGitHubRepository()).rejects.toThrow("Invalid response payload");
  expect(invoke).toHaveBeenNthCalledWith(1, "star_github_repository", {}, undefined);
});
