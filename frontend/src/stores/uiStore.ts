import { useEffect } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "system" | "light" | "dark";
interface UiState {
  navigationOpen: boolean;
  setNavigationOpen(value: boolean): void;
  collapsed: boolean;
  theme: Theme;
  speed: number;
  loop: boolean;
  popupPosition: { x: number; y: number } | null;
  setPopupPosition(value: UiState["popupPosition"]): void;
  setCollapsed(value: boolean): void;
  setTheme(value: Theme): void;
  setSpeed(value: number): void;
  setLoop(value: boolean): void;
}
export const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2] as const;
export const useUiStore = create<UiState>()(persist((set) => ({
  navigationOpen: false,
  setNavigationOpen: (navigationOpen) => { set({ navigationOpen }); },
  collapsed: false,
  theme: "system",
  speed: 1,
  loop: true,
  popupPosition: null,
  setPopupPosition: (popupPosition) => { set({ popupPosition }); },
  setCollapsed: (collapsed) => { set({ collapsed }); },
  setTheme: (theme) => { set({ theme }); },
  setSpeed: (speed) => { if (PLAYBACK_SPEEDS.some((value) => value === speed)) set({ speed }); },
  setLoop: (loop) => { set({ loop }); },
}), {
  name: "sharppy-workspace-ui",
  partialize: ({ collapsed, theme, speed, loop, popupPosition }) => ({ collapsed, theme, speed, loop, popupPosition }),
  merge: (saved, current) => {
    const value = saved !== null && typeof saved === "object" ? saved as Record<string, unknown> : {};
    const point = value.popupPosition !== null && typeof value.popupPosition === "object" ? value.popupPosition as Record<string, unknown> : {};
    return { ...current,
      collapsed: typeof value.collapsed === "boolean" ? value.collapsed : current.collapsed,
      theme: value.theme === "light" || value.theme === "dark" ? value.theme : "system",
      speed: PLAYBACK_SPEEDS.find((speed) => speed === value.speed) ?? 1,
      loop: typeof value.loop === "boolean" ? value.loop : current.loop,
      popupPosition: typeof point.x === "number" && Number.isFinite(point.x) && typeof point.y === "number" && Number.isFinite(point.y) ? { x: point.x, y: point.y } : null,
    };
  },
}));

export function useTheme(): void {
  const theme = useUiStore((state) => state.theme);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = (): void => { document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "dark" : "light") : theme; };
    apply();
    media.addEventListener("change", apply);
    return () => { media.removeEventListener("change", apply); };
  }, [theme]);
}
