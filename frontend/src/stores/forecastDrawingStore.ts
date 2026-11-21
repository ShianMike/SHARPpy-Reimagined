import { create, type StoreApi } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type { BackendClient } from "../lib/backend/client";
import { useHoverStore } from "../map/hover/hoverStore";
import { FORECAST_LEVELS, MAX_FORECAST_POLYGONS, MAX_FORECAST_VERTICES, type ForecastDrawingResult, type ForecastPoint, type ForecastPolygon, type ForecastProduct, type GeneratedForecastContour } from "../types/forecastDrawing";
import { useBoxSoundingStore } from "./boxSoundingStore";
import { copyForecastPoints, sameOutline, samePoint, samePoints, type ForecastPoints } from "./forecastDrawingPoints";
import { readForecastDrawing, writeForecastDrawing } from "./forecastDrawingStorage";
import { useForecastStore } from "./forecastStore";

type ForecastBackend = Pick<BackendClient, "buildForecastDrawing">;
type ForecastDrawingMode = "idle" | "draw" | "edit";
type ForecastEditTool = "reshape" | "move";

interface ForecastDrawingData {
  enabled: boolean;
  product: ForecastProduct;
  level: number;
  hatch: boolean;
  mode: ForecastDrawingMode;
  editTool: ForecastEditTool;
  points: ForecastPoints;
  undoSteps: readonly ForecastPoints[];
  redoSteps: readonly ForecastPoints[];
  editGesture: { id: symbol; points: ForecastPoints } | null;
  selectedId: string | null;
  editing: ForecastPolygon | null;
  polygons: Record<ForecastProduct, readonly ForecastPolygon[]>;
  results: Partial<Record<ForecastProduct, ForecastDrawingResult>>;
  saving: boolean;
  error: string | null;
  copiedOutline: { readonly level: number; readonly points: ForecastPoints } | null;
}

const emptyHistory = { undoSteps: [], redoSteps: [], editGesture: null } as const;
const emptySketch = { ...emptyHistory, mode: "idle", editTool: "reshape", points: [], editing: null } as const;
const tooManyAreas = `This outlook already has ${String(MAX_FORECAST_POLYGONS)} shapes. Remove a shape before adding another.`;
const failureMessage = (error: unknown): string =>
  error instanceof Error ? error.message : "The forecast drawing could not be validated.";

function canTransferOutline({ enabled, saving, mode }: ForecastDrawingData): boolean {
  return enabled && !saving && mode === "idle" && !useForecastStore.getState().exporting;
}

function appendStep(steps: readonly ForecastPoints[], points: ForecastPoints): readonly ForecastPoints[] {
  return [...steps, points].slice(-MAX_FORECAST_VERTICES);
}

/** Keep edited shapes when their source no longer generates that risk. */
function detachContours(polygons: readonly ForecastPolygon[], sourceId: string, sourceLevel = 0): readonly ForecastPolygon[] {
  return polygons.map((polygon) => {
    if (polygon.sourceId !== sourceId || polygon.level < sourceLevel) return polygon;
    const { id, level, hatch, points } = polygon;
    return { id, level, hatch, points };
  });
}

function createForecastDrawingState(set: StoreApi<ForecastDrawingData>["setState"], get: () => ForecastDrawingData) {
  const changePoints = (points: ForecastPoints, provisional = false): void => {
    set((state) => provisional ? { points, error: null } : {
      ...emptyHistory,
      points,
      undoSteps: appendStep(state.undoSteps, state.editGesture?.points ?? state.points),
      error: null,
    });
  };

  const stepHistory = (from: "undoSteps" | "redoSteps"): void => {
    const state = get();
    const points = state[from].at(-1);
    if (state.saving || state.editGesture !== null || points === undefined) return;
    const to = from === "undoSteps" ? "redoSteps" : "undoSteps";
    set({ points, [from]: state[from].slice(0, -1), [to]: appendStep(state[to], state.points), error: null });
  };

  const commit = async (backend: ForecastBackend, polygons: readonly ForecastPolygon[],
    selectedId: string | null, risk?: Pick<ForecastPolygon, "level" | "hatch">): Promise<boolean> => {
    const { product } = get();
    set({ saving: true, error: null });
    let result: ForecastDrawingResult;
    try {
      result = await backend.buildForecastDrawing(product, polygons);
    } catch (error) {
      set({ saving: false, error: failureMessage(error) });
      return false;
    }
    const saved = writeForecastDrawing(product, polygons);
    set((state) => ({
      ...emptySketch, ...risk,
      polygons: { ...state.polygons, [product]: polygons },
      results: { ...state.results, [product]: result },
      selectedId: state.enabled ? selectedId : null,
      saving: false,
      error: saved ? null : "Device storage is unavailable. This drawing is saved for this session.",
    }));
    return true;
  };
  const initial: ForecastDrawingData = {
    enabled: false,
    product: "cat",
    level: 1,
    hatch: false,
    ...emptySketch,
    selectedId: null,
    polygons: {
      cat: readForecastDrawing("cat"),
      torn: readForecastDrawing("torn"),
      hail: readForecastDrawing("hail"),
      wind: readForecastDrawing("wind"),
    },
    results: {},
    saving: false,
    error: null,
    copiedOutline: null,
  };
  return {
    ...initial,
    setEnabled: (enabled: boolean) => {
      if (get().enabled === enabled) return;
      if (enabled) {
        useBoxSoundingStore.getState().setMode("point");
        useForecastStore.getState().setPlaying(false);
        useHoverStore.getState().setInspecting(false);
      }
      set({ enabled, ...emptySketch, selectedId: null, error: null });
    },
    setProduct: (product: ForecastProduct) => {
      const state = get();
      if (state.saving || product === state.product) return;
      set({ product, level: FORECAST_LEVELS[product][0] ?? 0, hatch: false, ...emptySketch, selectedId: null, error: null });
    },
    setRisk: (level: number, hatch: boolean) => {
      const { saving, editing } = get();
      if (!saving && editing?.sourceId === undefined) set({ level, hatch, error: null });
    },
    setEditTool: (editTool: ForecastEditTool) => {
      const state = get();
      if (state.enabled && !state.saving && state.mode !== "draw" && state.editTool !== editTool) set({ editTool });
    },
    startDraw: () => {
      const state = get();
      if (state.saving || state.mode !== "idle") return;
      if (state.polygons[state.product].length >= MAX_FORECAST_POLYGONS) {
        set({ error: tooManyAreas });
        return;
      }
      set({ ...emptySketch, mode: "draw", selectedId: null, error: null });
    },
    selectPolygon: (id: string) => {
      const state = get();
      if (state.saving || state.mode !== "idle") return;
      const selected = state.polygons[state.product].find((polygon) => polygon.id === id);
      if (selected !== undefined) set({ selectedId: id, level: selected.level, hatch: selected.hatch, error: null });
    },
    startEdit: (target: string | GeneratedForecastContour): boolean => {
      const state = get();
      const { editing, level, hatch, points } = state;
      if (state.saving || state.mode === "draw" || state.editGesture !== null
        || (editing !== null && (points !== editing.points || level !== editing.level || hatch !== editing.hatch))) return false;
      const authored = state.polygons[state.product];
      if (typeof target !== "string" && authored.length >= MAX_FORECAST_POLYGONS) {
        set({ error: tooManyAreas });
        return false;
      }
      let selected: ForecastPolygon | undefined;
      if (typeof target === "string") {
        selected = authored.find((polygon) => polygon.id === target);
      } else if (editing?.points === target.points && editing.sourceId === target.sourceId && editing.level === target.level) {
        selected = editing;
      } else {
        selected = { ...target, id: crypto.randomUUID(), hatch: false };
      }
      if (selected === undefined) return false;
      if (selected === editing) {
        set({ error: null });
        return true;
      }
      set({ ...emptyHistory, mode: "edit", points: selected.points, selectedId: selected.id, editing: selected,
        level: selected.level, hatch: selected.hatch, error: null });
      return true;
    },
    copyCategoricalOutline: (target: string | GeneratedForecastContour): boolean => {
      const state = get();
      if (state.product !== "cat" || !canTransferOutline(state)) return false;
      const outline = typeof target === "string"
        ? state.polygons.cat.find((polygon) => polygon.id === target && !polygon.hatch)
        : state.results.cat?.generatedContours.find((contour) => contour === target);
      if (outline === undefined || !FORECAST_LEVELS.cat.some((level) => level > 0 && level === outline.level)) return false;
      set({ copiedOutline: { level: outline.level, points: copyForecastPoints(outline.points) }, error: null });
      return true;
    },
    pasteCategoricalOutline: async (backend: ForecastBackend, probability?: number): Promise<boolean> => {
      const state = get();
      const { product, copiedOutline } = state;
      const level = probability ?? (state.hatch ? undefined : state.level);
      if (!canTransferOutline(state) || copiedOutline === null || product === "cat" || level === undefined || !FORECAST_LEVELS[product].includes(level)) return false;
      const areas = state.polygons[product];
      if (areas.length >= MAX_FORECAST_POLYGONS) {
        set({ error: tooManyAreas });
        return false;
      }
      if (areas.some((area) => !area.hatch && area.level === level && sameOutline(area.points, copiedOutline.points))) {
        set({ error: "This probability already has the copied outline." });
        return false;
      }
      const polygon: ForecastPolygon = { id: crypto.randomUUID(), level, hatch: false, points: copyForecastPoints(copiedOutline.points) };
      return commit(backend, [...areas, polygon], polygon.id, { level, hatch: false });
    },
    addVertex: (point: ForecastPoint) => {
      const state = get();
      if (state.saving || state.mode !== "draw") return;
      if (state.points.length >= MAX_FORECAST_VERTICES) {
        set({ error: `Finish the shape at the ${String(MAX_FORECAST_VERTICES)}-vertex limit.` });
        return;
      }
      if (samePoint(point, state.points.at(-1))) return;
      changePoints([...state.points, point]);
    },
    insertVertex: (afterIndex: number, point: ForecastPoint) => {
      const state = get();
      if (state.saving || state.mode !== "edit" || state.editGesture !== null || state.points[afterIndex] === undefined
        || state.points.length >= MAX_FORECAST_VERTICES) return;
      changePoints(state.points.toSpliced(afterIndex + 1, 0, point));
    },
    moveVertex: (index: number, point: ForecastPoint, gesture?: symbol) => {
      const state = get();
      const current = state.points[index];
      if (state.saving || state.mode !== "edit" || state.editGesture?.id !== gesture || current === undefined
        || samePoint(point, current)) return;
      changePoints(state.points.with(index, point), gesture !== undefined);
    },
    removeVertex: (index: number) => {
      const state = get();
      if (state.saving || state.mode !== "edit" || state.editGesture !== null
        || state.points.length <= 3 || state.points[index] === undefined) return;
      changePoints(state.points.toSpliced(index, 1));
    },
    replaceEditPoints: (points: ForecastPoints, gesture?: symbol) => {
      const state = get();
      if (state.saving || state.mode !== "edit" || state.editGesture?.id !== gesture || samePoints(state.points, points)) return;
      changePoints(points, gesture !== undefined);
    },
    undo: () => { stepHistory("undoSteps"); },
    redo: () => { stepHistory("redoSteps"); },
    beginEditGesture: () => {
      const state = get();
      if (state.saving || state.mode !== "edit" || state.editGesture !== null) return null;
      const id = Symbol();
      set({ editGesture: { id, points: state.points } });
      return id;
    },
    endEditGesture: (id: symbol, cancel = false) => {
      const { editGesture, points } = get();
      if (editGesture?.id !== id) return;
      if (cancel || samePoints(editGesture.points, points)) {
        set({ points: editGesture.points, editGesture: null });
        return;
      }
      changePoints(points);
    },
    cancel: () => {
      const { saving, product, polygons, selectedId } = get();
      if (!saving) set({ ...emptySketch, error: null,
        selectedId: polygons[product].some((polygon) => polygon.id === selectedId) ? selectedId : null });
    },
    restore: async (backend: ForecastBackend) => {
      const { enabled, saving, product, polygons, results } = get();
      if (!enabled || saving || results[product] !== undefined || polygons[product].length === 0) return;
      set({ saving: true, error: null });
      await backend.buildForecastDrawing(product, polygons[product]).then((result) => {
        set((state) => ({ saving: false, results: { ...state.results, [product]: result } }));
      }, (error: unknown) => { set({ saving: false, error: failureMessage(error) }); });
    },
    finish: async (backend: ForecastBackend) => {
      const state = get();
      if (!state.enabled || state.saving || state.mode === "idle" || state.editGesture !== null || state.points.length < 3) return;
      const { product, points, level, hatch, editing } = state;
      const id = editing?.id ?? crypto.randomUUID();
      const authored = state.polygons[product];
      const index = authored.findIndex((area) => area.id === id);
      if (editing?.sourceId !== undefined && points === editing.points) {
        set({ ...emptySketch, selectedId: index === -1 ? null : id, error: null });
        return;
      }
      const polygon: ForecastPolygon = { ...editing, id, level, hatch, points };
      const updated = index === -1 ? [...authored, polygon] : authored.with(index, polygon);
      const changedSource = editing?.hatch === false && (hatch || level < editing.level);
      await commit(backend, changedSource ? detachContours(updated, id, hatch ? 0 : level) : updated, id);
    },
    deletePolygon: async (id: string, backend: ForecastBackend) => {
      const state = get();
      if (!state.enabled || state.saving || state.mode !== "idle") return;
      const authored = state.polygons[state.product];
      const next = authored.filter((area) => area.id !== id);
      if (next.length !== authored.length) await commit(backend, detachContours(next, id), state.selectedId === id ? null : state.selectedId);
    },
  };
}

export const useForecastDrawingStore = create<ReturnType<typeof createForecastDrawingState>>()(subscribeWithSelector(createForecastDrawingState));
