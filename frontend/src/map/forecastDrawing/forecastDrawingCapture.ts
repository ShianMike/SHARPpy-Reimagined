import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import type { ForecastDrawingDetails, ForecastProduct } from "../../types/forecastDrawing";

export type ForecastDrawingCapture = ({ kind: "standard" } | { kind: "unofficial"; product: ForecastProduct; details: ForecastDrawingDetails }) & {
  signal: AbortSignal;
  dispose(): void;
};
export type UnofficialForecastDrawingCapture = Extract<ForecastDrawingCapture, { kind: "unofficial" }>;

function visibleDrawing() {
  const drawing = useForecastDrawingStore.getState();
  return drawing.enabled && useMapStore.getState().view === "map" && useAppStore.getState().activeWorkspace === "map" ? drawing : null;
}

/** Sounding locators cannot carry the full disclaimer at their inset size. */
export function assertPlainMapCapture(): void {
  const drawing = visibleDrawing();
  if (drawing !== null && (drawing.mode !== "idle" || drawing.saving || (drawing.results[drawing.product]?.areas.length ?? 0) > 0)) {
    throw new Error("Turn off Draw Forecast to capture a sounding locator map.");
  }
}

/** Pin the rendered product, areas, and display style for the entire export, including encoding. */
export function trackForecastDrawingCapture(signal: AbortSignal): ForecastDrawingCapture {
  signal.throwIfAborted();
  const initial = visibleDrawing();
  if (initial !== null && (initial.mode !== "idle" || initial.saving)) {
    throw new Error("Finish or cancel the current forecast drawing before exporting.");
  }
  const product = initial?.product;
  const areas = initial?.results[initial.product]?.areas;
  if (initial !== null && areas === undefined && initial.polygons[initial.product].length > 0) {
    throw new Error("The saved forecast drawing is awaiting validation. Try again after it loads.");
  }
  const content = product !== undefined && areas !== undefined && areas.length > 0
    ? { kind: "unofficial", product, details: useUiStore.getState().forecastDrawingDetails } as const : { kind: "standard" } as const;
  const changed = new AbortController();
  const check = (): void => {
    const current = visibleDrawing();
    if (current?.product !== product || current?.results[current.product]?.areas !== areas
      || current?.mode !== initial?.mode || current?.saving !== initial?.saving) {
      changed.abort(new Error("The forecast drawing changed during export. Try again."));
    }
  };
  const unsubscribeDrawing = useForecastDrawingStore.subscribe(check);
  const unsubscribeMap = useMapStore.subscribe((state) => state.view, check);
  const unsubscribeWorkspace = useAppStore.subscribe((state) => state.activeWorkspace, check);
  const unsubscribeDisplay = content.kind === "unofficial" ? useUiStore.subscribe((state, previous) => {
    if (state.forecastOutlinesOnly !== previous.forecastOutlinesOnly) {
      changed.abort(new Error("The forecast display changed during export. Try again."));
    }
  }) : undefined;
  return { ...content, signal: AbortSignal.any([signal, changed.signal]), dispose: () => {
    unsubscribeDrawing(); unsubscribeMap(); unsubscribeWorkspace(); unsubscribeDisplay?.();
  } };
}
