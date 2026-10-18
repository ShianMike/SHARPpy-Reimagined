// A panel's model field (REQUEST §14): its product (chosen, or a default
// that differs per panel), whether a frame should be drawn, and the frame
// query. The map panel, its chip, the layer panel, and the timeline all read
// it, and share one query per product, run, and hour.

import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";
import { useForecastContext, type ForecastContext } from "../features/forecast/useForecast";
import { panelLayers, panelProduct, useMapStore } from "../stores/mapStore";
import type { FieldFrame, FieldProduct, MapCatalog } from "../types/map";
import { useFieldFrame, useMapCatalog, type FrameRequest } from "./mapQueries";

/**
 * The catalogue's default for panel 0; for panel n, the first product of the
 * category n places after the default's, so a new four-panel view starts on
 * four kinds of field.
 */
export function defaultProductFor(catalog: MapCatalog, panel: number): FieldProduct | undefined {
  const fallback = catalog.products.find((product) => product.key === catalog.defaultProduct);
  if (panel === 0 || fallback === undefined || catalog.categories.length === 0) {
    return fallback;
  }
  const start = Math.max(0, catalog.categories.indexOf(fallback.category));
  const category = catalog.categories[(start + panel) % catalog.categories.length];
  return catalog.products.find((product) => product.category === category) ?? fallback;
}

/** Why a panel draws no field, or null when it should. */
export type FieldBlock = "hidden" | "no-catalog" | "other-model" | "loading-models";

export interface PanelField {
  product: FieldProduct | null;
  /** The frame to draw; null when blocked. */
  request: FrameRequest | null;
  blocked: FieldBlock | null;
  frame: UseQueryResult<FieldFrame>;
  catalog: UseQueryResult<MapCatalog>;
}

/** The product a panel shows: its choice when the catalogue lists it, else its default. */
export function productOf(catalog: MapCatalog | undefined, chosen: string | null, panel: number): FieldProduct | null {
  if (catalog === undefined) {
    return null;
  }
  return catalog.products.find((product) => product.key === chosen) ?? defaultProductFor(catalog, panel) ?? null;
}

function blockOf(visible: boolean, catalog: MapCatalog | undefined, context: ForecastContext | null): FieldBlock | null {
  if (!visible) {
    return "hidden";
  }
  if (catalog === undefined) {
    return "no-catalog";
  }
  if (context === null) {
    return "loading-models";
  }
  return context.fieldsAvailable ? null : "other-model";
}

export function usePanelField(panel: number): PanelField {
  const catalog = useMapCatalog();
  const context = useForecastContext();
  const chosen = useMapStore((state) => panelProduct(state, panel));
  const visible = useMapStore((state) => panelLayers(state, panel)["model-field"].visible);
  const live = useMapStore((state) => state.view === "live");
  const product = productOf(catalog.data, chosen, panel);
  const blocked = blockOf(visible && !live, catalog.data, context);
  const request =
    blocked === null && product !== null && context !== null
      ? { model: context.model.key, product: product.key, runTime: context.run, forecastHour: context.hour }
      : null;
  return { product, request, blocked, frame: useFieldFrame(request), catalog };
}

/**
 * The products the visible panels draw now, without repeats (playback waits
 * for and prefetches them). A stable array while they do not change.
 */
export function useDrawnProducts(): readonly FieldProduct[] {
  const catalog = useMapCatalog().data;
  const fieldsAvailable = useForecastContext()?.fieldsAvailable === true;
  const layout = useMapStore((state) => state.panelLayout);
  // A string, so the selector's result compares by value.
  const chosen = useMapStore((state) =>
    Array.from({ length: layout }, (_, panel) =>
      panelLayers(state, panel)["model-field"].visible ? (panelProduct(state, panel) ?? "*") : "",
    ).join("|"),
  );
  return useMemo(() => {
    if (catalog === undefined || !fieldsAvailable) {
      return [];
    }
    const products = chosen.split("|").map((entry, panel) =>
      entry === "" ? null : productOf(catalog, entry === "*" ? null : entry, panel),
    );
    return [...new Set(products.filter((product): product is FieldProduct => product !== null))];
  }, [catalog, fieldsAvailable, chosen]);
}
