import { useEffect } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_UNITS, parseUnitPreferences, type UnitPreferences } from "../lib/displayUnits";

export type Theme = "system" | "light" | "dark";
type PopupPosition = { x: number; y: number } | null;
interface UiState {
  navigationOpen: boolean;
  setNavigationOpen(value: boolean): void;
  collapsed: boolean;
  theme: Theme;
  speed: number;
  loop: boolean;
  smoothPlayback: boolean;
  units: UnitPreferences;
  setUnits(value: UnitPreferences): void;
  popupPositions: Record<string, PopupPosition>;
  setPopupPosition(id: string, value: PopupPosition): void;
  setCollapsed(value: boolean): void;
  setTheme(value: Theme): void;
  setSpeed(value: number): void;
  setLoop(value: boolean): void;
  setSmoothPlayback(value: boolean): void;
}
export const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2] as const;
export const useUiStore = create<UiState>()(persist((set) => ({
  navigationOpen: false,
  setNavigationOpen: (navigationOpen) => { set({ navigationOpen }); },
  collapsed: false,
  theme: "system",
  speed: 1,
  loop: true,
  smoothPlayback: false,
  units: DEFAULT_UNITS,
  setUnits: (units) => { set({ units: parseUnitPreferences(units) }); },
  popupPositions: {},
  setPopupPosition: (id, position) => { set((state) => ({ popupPositions: { ...state.popupPositions, [id]: position } })); },
  setCollapsed: (collapsed) => { set({ collapsed }); },
  setTheme: (theme) => { set({ theme }); },
  setSpeed: (speed) => { if (PLAYBACK_SPEEDS.some((value) => value === speed)) set({ speed }); },
  setLoop: (loop) => { set({ loop }); },
  setSmoothPlayback: (smoothPlayback) => { set({ smoothPlayback }); },
}), {
  name: "sharppy-workspace-ui",
  partialize: ({ collapsed, theme, speed, loop, smoothPlayback, popupPositions, units }) => ({ collapsed, theme, speed, loop, smoothPlayback, popupPositions, units }),
  merge: (saved, current) => {
    const value = saved !== null && typeof saved === "object" ? saved as Record<string, unknown> : {};
    const positions = value.popupPositions !== null && typeof value.popupPositions === "object" ? value.popupPositions as Record<string, unknown> : {};
    return { ...current,
      collapsed: typeof value.collapsed === "boolean" ? value.collapsed : current.collapsed,
      theme: value.theme === "light" || value.theme === "dark" ? value.theme : "system",
      speed: PLAYBACK_SPEEDS.find((speed) => speed === value.speed) ?? 1,
      loop: typeof value.loop === "boolean" ? value.loop : current.loop,
      smoothPlayback: typeof value.smoothPlayback === "boolean" ? value.smoothPlayback : current.smoothPlayback,
      units: parseUnitPreferences(value.units),
      popupPositions: Object.fromEntries(Object.entries(positions).flatMap(([id, point]) => {
        if (point === null || typeof point !== "object") return [];
        const { x, y } = point as Record<string, unknown>;
        return typeof x === "number" && Number.isFinite(x) && typeof y === "number" && Number.isFinite(y) ? [[id, { x, y }]] : [];
      })),
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
