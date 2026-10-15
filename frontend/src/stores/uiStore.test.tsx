// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useTheme, useUiStore } from "./uiStore";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useUiStore.setState({ theme: "system", speed: 1, loop: true, collapsed: false, popupPosition: null }); });

it("validates persisted workspace settings and ignores transient navigation state", () => {
  const state = useUiStore.getState();
  const merge = useUiStore.persist.getOptions().merge;
  if (merge === undefined) throw new Error("Persistence requires a validating merge");
  expect(merge({ theme: "invalid", speed: -1, loop: "false", collapsed: "yes", navigationOpen: true }, state)).toMatchObject({ theme: "system", speed: 1, loop: true, collapsed: false, navigationOpen: false });
  expect(merge({ theme: "light", speed: 2, loop: false, collapsed: true }, state)).toMatchObject({ theme: "light", speed: 2, loop: false, collapsed: true });
  expect(merge({ popupPosition: { x: 150, y: 230 } }, state)).toMatchObject({ popupPosition: { x: 150, y: 230 } });
  expect(merge({ popupPosition: { x: Infinity, y: "12" } }, state)).toMatchObject({ popupPosition: null });
  useUiStore.getState().setPopupPosition({ x: 150, y: 230 });
  expect(useUiStore.persist.getOptions().partialize?.(useUiStore.getState())).toMatchObject({ popupPosition: { x: 150, y: 230 } });
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
