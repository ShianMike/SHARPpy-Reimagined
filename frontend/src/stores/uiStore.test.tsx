// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useTheme, useUiStore } from "./uiStore";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useUiStore.setState({ lastVersion: null, whatsNewOpen: false, theme: "system", speed: 1, loop: true, smoothPlayback: false, collapsed: false, popupPositions: {} }); });

it("validates persisted workspace settings and ignores transient navigation state", () => {
  const state = useUiStore.getState();
  const merge = useUiStore.persist.getOptions().merge;
  if (merge === undefined) throw new Error("Persistence requires a validating merge");
  expect(merge({ theme: "invalid", speed: -1, loop: "false", collapsed: "yes", navigationOpen: true }, state)).toMatchObject({ theme: "system", speed: 1, loop: true, collapsed: false, navigationOpen: false });
  expect(merge({ theme: "light", speed: 2, loop: false, collapsed: true }, state)).toMatchObject({ theme: "light", speed: 2, loop: false, collapsed: true });
  expect(merge({ smoothPlayback: "yes" }, state)).toMatchObject({ smoothPlayback: false });
  expect(merge({ smoothPlayback: true }, state)).toMatchObject({ smoothPlayback: true });
  expect(merge({ lastVersion: "3.1.0", whatsNewOpen: true }, state)).toMatchObject({ lastVersion: "3.1.0", whatsNewOpen: false });
  expect(merge({ lastVersion: { invalid: true } }, state)).toMatchObject({ lastVersion: null });
  expect(merge({ lastVersion: "corrupted" }, state)).toMatchObject({ lastVersion: null });
  useUiStore.getState().setSmoothPlayback(true);
  expect(useUiStore.persist.getOptions().partialize?.(useUiStore.getState())).toMatchObject({ smoothPlayback: true });
  expect(merge({ popupPositions: { model: { x: 150, y: 230 }, parameter: { x: 80, y: 90 }, run: { x: Infinity, y: "12" }, hour: null } }, state)).toMatchObject({ popupPositions: { model: { x: 150, y: 230 }, parameter: { x: 80, y: 90 } } });
  expect(merge({ popupPosition: { x: 150, y: 230 } }, state)).toMatchObject({ popupPositions: {} });
  useUiStore.getState().setPopupPosition("model", { x: 150, y: 230 });
  expect(useUiStore.persist.getOptions().partialize?.(useUiStore.getState())).toMatchObject({ popupPositions: { model: { x: 150, y: 230 } } });
});

it("follows the system theme and stops following when a theme is chosen", () => {
  const listeners = new Set<() => void>();
  const media = { matches: true, addEventListener: (_: string, listener: () => void) => { listeners.add(listener); }, removeEventListener: (_: string, listener: () => void) => { listeners.delete(listener); } };
  vi.stubGlobal("matchMedia", () => media);
  function Theme(): null { useTheme(); return null; }
  render(<Theme />);
  expect(document.documentElement.dataset.theme).toBe("dark");
  act(() => { media.matches = false; listeners.forEach((listener) => { listener(); }); });
  expect(document.documentElement.dataset.theme).toBe("light");
  act(() => { useUiStore.getState().setTheme("dark"); });
  act(() => { listeners.forEach((listener) => { listener(); }); });
  expect(document.documentElement.dataset.theme).toBe("dark");
  cleanup();
  expect(listeners.size).toBe(0);
});

it("keeps startup and release-highlight dismissal usable when storage writes fail", () => {
  useUiStore.setState({ lastVersion: "3.0.0", whatsNewOpen: false });
  const save = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage full"); });
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  try {
    expect(() => { useUiStore.getState().prepareReleaseNotes("3.1.0", true); }).not.toThrow();
    expect(useUiStore.getState()).toMatchObject({ lastVersion: "3.1.0", whatsNewOpen: true });
    expect(() => { useUiStore.getState().setWhatsNewOpen(false); }).not.toThrow();
    expect(useUiStore.getState().whatsNewOpen).toBe(false);
    expect(warn).toHaveBeenCalledTimes(2);
  } finally { save.mockRestore(); warn.mockRestore(); }
});
