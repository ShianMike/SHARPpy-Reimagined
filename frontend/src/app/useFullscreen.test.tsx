// @vitest-environment jsdom
import { act, cleanup, fireEvent, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLogStore } from "../stores/logStore";
import { useFullscreen } from "./useFullscreen";

const native = vi.hoisted(() => ({ isFullscreen: vi.fn<() => Promise<boolean>>(), setFullscreen: vi.fn<(value: boolean) => Promise<void>>() }));
vi.mock("../lib/backend/tauriTransport", () => ({ getCurrentWindow: () => native }));

let nativeFullscreen = false;
beforeEach(() => {
  nativeFullscreen = false;
  native.isFullscreen.mockImplementation(() => Promise.resolve(nativeFullscreen));
  native.setFullscreen.mockImplementation((value) => { nativeFullscreen = value; return Promise.resolve(); });
  Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
  Object.defineProperty(document.documentElement, "requestFullscreen", { configurable: true, value: vi.fn(() => {
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: document.documentElement });
    document.dispatchEvent(new Event("fullscreenchange"));
    return Promise.resolve();
  }) });
  Object.defineProperty(document, "exitFullscreen", { configurable: true, value: vi.fn(() => {
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
    document.dispatchEvent(new Event("fullscreenchange"));
    return Promise.resolve();
  }) });
  useLogStore.getState().clear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("enters and exits the desktop window, follows native resize, and supports F11/Escape", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  const { result } = renderHook(useFullscreen);
  await act(async () => { await result.current.toggle(); });
  expect(native.setFullscreen).toHaveBeenLastCalledWith(true);
  expect(result.current.fullscreen).toBe(true);
  await act(async () => { fireEvent.keyDown(document, { key: "Escape" }); await Promise.resolve(); });
  expect(native.setFullscreen).toHaveBeenLastCalledWith(false);
  await act(async () => { fireEvent.keyDown(document, { key: "F11" }); await Promise.resolve(); });
  expect(result.current.fullscreen).toBe(true);
  nativeFullscreen = false;
  await act(async () => { window.dispatchEvent(new Event("resize")); await Promise.resolve(); });
  expect(result.current.fullscreen).toBe(false);
  const count = native.setFullscreen.mock.calls.length;
  await act(async () => {
    fireEvent.keyDown(document, { key: "F11", repeat: true });
    fireEvent.keyDown(document, { key: "F11", ctrlKey: true });
    const claimed = new KeyboardEvent("keydown", { key: "F11", cancelable: true });
    claimed.preventDefault(); document.dispatchEvent(claimed);
    await Promise.resolve();
  });
  expect(native.setFullscreen).toHaveBeenCalledTimes(count);
});

it("uses browser fullscreen and follows external exits", async () => {
  const { result } = renderHook(useFullscreen);
  await act(async () => { await result.current.toggle(); });
  expect(document.fullscreenElement).toBe(document.documentElement);
  expect(result.current.fullscreen).toBe(true);
  await act(async () => { await result.current.toggle(); });
  expect(document.fullscreenElement).toBeNull();
  await act(async () => { await result.current.toggle(); });
  await act(async () => { await document.exitFullscreen(); });
  expect(result.current.fullscreen).toBe(false);
  expect(native.setFullscreen).not.toHaveBeenCalled();
});

it("reports a denied native request, keeps the current state, and lets the user retry", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  native.setFullscreen.mockRejectedValueOnce("denied");
  const { result } = renderHook(useFullscreen);
  await act(async () => { await result.current.toggle(); });
  expect(result.current.fullscreen).toBe(false);
  expect(result.current.busy).toBe(false);
  expect(result.current.error).toBe("Could not change fullscreen. Try again.");
  expect(useLogStore.getState().entries[0]?.detail).toBe("denied");
  await act(async () => { await result.current.toggle(); });
  await waitFor(() => { expect(result.current.fullscreen).toBe(true); });
  expect(result.current.error).toBe("");
});
