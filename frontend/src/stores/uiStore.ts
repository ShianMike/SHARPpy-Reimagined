import { useEffect } from "react";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { DEFAULT_UNITS, parseUnitPreferences, type UnitPreferences } from "../lib/displayUnits";
import { FORECAST_OUTLOOK_DAYS, MAX_FORECAST_AUTHOR_LENGTH, type ForecastDrawingDetails } from "../types/forecastDrawing";
import type { SoundingColorStyle } from "../types/soundings";
import { DEFAULT_FORECAST_EXPORT_SETTINGS, parseForecastExportSettings, type ForecastExportSettings } from "./forecastExportSettings";
import { DEFAULT_GITHUB_STAR_PREFERENCE, parseGitHubStarPreference, type GitHubStarPreference } from "./githubStarPreferences";

export type Theme = "system" | "light" | "dark";
type PopupPosition = { x: number; y: number } | null;
interface UiState {
  lastUpdateCheckWindow: number | null;
  setLastUpdateCheckWindow(value: number): void;
  lastVersion: string | null;
  whatsNewOpen: boolean;
  prepareReleaseNotes(version: string, returning: boolean): void;
  setWhatsNewOpen(value: boolean): void;
  navigationOpen: boolean;
  setNavigationOpen(value: boolean): void;
  inspectorSourceOpen: boolean;
  setInspectorSourceOpen(value: boolean): void;
  collapsed: boolean;
  theme: Theme;
  soundingColorStyle: SoundingColorStyle;
  speed: number;
  loop: boolean;
  smoothPlayback: boolean;
  units: UnitPreferences;
  setUnits(value: UnitPreferences): void;
  forecastExport: ForecastExportSettings;
  setForecastExport: (value: Partial<ForecastExportSettings>) => void;
  forecastOutlinesOnly: boolean;
  setForecastOutlinesOnly(value: boolean): void;
  forecastDrawingDetails: ForecastDrawingDetails;
  setForecastDrawingDetails(value: ForecastDrawingDetails): void;
  popupPositions: Record<string, PopupPosition>;
  setPopupPosition(id: string, value: PopupPosition): void;
  githubStar: GitHubStarPreference;
  setGitHubStar(value: GitHubStarPreference): void;
  setCollapsed(value: boolean): void;
  setTheme(value: Theme): void;
  setSoundingColorStyle(value: SoundingColorStyle): void;
  setSpeed(value: number): void;
  setLoop(value: boolean): void;
  setSmoothPlayback(value: boolean): void;
}

function cleanForecastAuthor(name: string): string {
  return Array.from(name.trim().replace(/\s+/gu, " ")).slice(0, MAX_FORECAST_AUTHOR_LENGTH).join("");
}

export const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2, 3, 4, 5, 6] as const;
export const useUiStore = create<UiState>()(persist((set, get) => ({
  lastUpdateCheckWindow: null,
  setLastUpdateCheckWindow: (lastUpdateCheckWindow) => { set({ lastUpdateCheckWindow }); },
  lastVersion: null,
  whatsNewOpen: false,
  prepareReleaseNotes: (version, returning) => {
    const previous = get().lastVersion;
    if (previous === version) return;
    set({ lastVersion: version, whatsNewOpen: previous !== null || returning });
  },
  setWhatsNewOpen: (whatsNewOpen) => {
    set({ whatsNewOpen });
  },
  navigationOpen: false,
  setNavigationOpen: (navigationOpen) => { set({ navigationOpen }); },
  inspectorSourceOpen: false,
  setInspectorSourceOpen: (inspectorSourceOpen) => { set({ inspectorSourceOpen }); },
  collapsed: false,
  theme: "system",
  soundingColorStyle: "standard",
  speed: 1,
  loop: true,
  smoothPlayback: false,
  units: DEFAULT_UNITS,
  setUnits: (units) => { set({ units: parseUnitPreferences(units) }); },
  forecastExport: DEFAULT_FORECAST_EXPORT_SETTINGS,
  setForecastExport: (value) => { set(state => ({ forecastExport: { ...state.forecastExport, ...value } })); },
  forecastOutlinesOnly: false,
  setForecastOutlinesOnly: (forecastOutlinesOnly) => { set({ forecastOutlinesOnly }); },
  forecastDrawingDetails: { author: "", day: null },
  setForecastDrawingDetails: ({ author, day }) => { set({ forecastDrawingDetails: { author: cleanForecastAuthor(author), day } }); },
  popupPositions: {},
  setPopupPosition: (id, position) => { set((state) => ({ popupPositions: { ...state.popupPositions, [id]: position } })); },
  githubStar: DEFAULT_GITHUB_STAR_PREFERENCE,
  setGitHubStar: (githubStar) => { set({ githubStar: parseGitHubStarPreference(githubStar) }); },
  setCollapsed: (collapsed) => { set({ collapsed }); },
  setTheme: (theme) => { set({ theme }); },
  setSoundingColorStyle: (soundingColorStyle) => { set({ soundingColorStyle }); },
  setSpeed: (speed) => { if (PLAYBACK_SPEEDS.some((value) => value === speed)) set({ speed }); },
  setLoop: (loop) => { set({ loop }); },
  setSmoothPlayback: (smoothPlayback) => { set({ smoothPlayback }); },
}), {
  name: "sharppy-workspace-ui",
  storage: createJSONStorage(() => {
    let memory: string | null = null;
    let unavailable = false;
    const access = (operation: () => void): void => {
      if (unavailable) return;
      try { operation(); }
      catch (error) {
        unavailable = true;
        console.warn(`[preferences] UI settings last for this session only (${String(error)})`);
      }
    };
    return {
      getItem: (name) => { access(() => { memory = localStorage.getItem(name); }); return memory; },
      setItem: (name, value) => { memory = value; access(() => { localStorage.setItem(name, value); }); },
      removeItem: (name) => { memory = null; access(() => { localStorage.removeItem(name); }); },
    };
  }),
  partialize: ({ lastVersion, lastUpdateCheckWindow, collapsed, theme, soundingColorStyle, speed, loop, smoothPlayback,
    popupPositions, githubStar, units, forecastExport, forecastOutlinesOnly, forecastDrawingDetails }) => ({
    lastVersion, lastUpdateCheckWindow, collapsed, theme, soundingColorStyle, speed, loop, smoothPlayback,
    popupPositions, githubStar, units, forecastExport, forecastOutlinesOnly, forecastDrawingDetails,
  }),
  merge: (saved, current) => {
    const value = saved !== null && typeof saved === "object" ? saved as Record<string, unknown> : {};
    const positions = value.popupPositions !== null && typeof value.popupPositions === "object" ? value.popupPositions as Record<string, unknown> : {};
    const drawing = value.forecastDrawingDetails;
    const details = drawing !== null && typeof drawing === "object" ? drawing as Record<string, unknown> : {};
    return { ...current,
      lastUpdateCheckWindow: typeof value.lastUpdateCheckWindow === "number" && Number.isSafeInteger(value.lastUpdateCheckWindow) && value.lastUpdateCheckWindow >= 0 ? value.lastUpdateCheckWindow : null,
      lastVersion: typeof value.lastVersion === "string" && /^\d+\.\d+\.\d+(?:-[\w.-]+)?(?:\+[\w.-]+)?$/u.test(value.lastVersion) ? value.lastVersion : null,
      collapsed: typeof value.collapsed === "boolean" ? value.collapsed : current.collapsed,
      theme: value.theme === "light" || value.theme === "dark" ? value.theme : "system",
      soundingColorStyle: value.soundingColorStyle === "inverted" || value.soundingColorStyle === "protanopia" ? value.soundingColorStyle : "standard",
      speed: PLAYBACK_SPEEDS.find((speed) => speed === value.speed) ?? 1,
      loop: typeof value.loop === "boolean" ? value.loop : current.loop,
      smoothPlayback: typeof value.smoothPlayback === "boolean" ? value.smoothPlayback : current.smoothPlayback,
      units: parseUnitPreferences(value.units),
      forecastExport: parseForecastExportSettings(value.forecastExport),
      forecastOutlinesOnly: typeof value.forecastOutlinesOnly === "boolean" ? value.forecastOutlinesOnly : current.forecastOutlinesOnly,
      forecastDrawingDetails: {
        author: typeof details.author === "string" ? cleanForecastAuthor(details.author) : "",
        day: FORECAST_OUTLOOK_DAYS.find((day) => day === details.day) ?? null,
      },
      popupPositions: Object.fromEntries(Object.entries(positions).flatMap(([id, point]) => {
        if (point === null || typeof point !== "object") return [];
        const { x, y } = point as Record<string, unknown>;
        return typeof x === "number" && Number.isFinite(x) && typeof y === "number" && Number.isFinite(y) ? [[id, { x, y }]] : [];
      })),
      githubStar: parseGitHubStarPreference(value.githubStar),
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
