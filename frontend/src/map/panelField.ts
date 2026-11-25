// A panel's model field (REQUEST §14): its product (chosen, or a default
// that differs per panel), whether a frame should be drawn, and the frame
// query. The map panel, its chip, the layer panel, and the timeline all read
// it, and share one query per product, run, and hour.

import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";
import { useForecastContext, type ForecastContext } from "../features/forecast/useForecast";
import { panelLayers, panelProduct, useMapStore } from "../stores/mapStore";
import type { FieldFrame, FieldProduct, FieldVariant, MapCatalog } from "../types/map";
import { useFieldFrame, useMapCatalog, type FrameRequest } from "./mapQueries";

/**
 * The catalogue's default for panel 0; for panel n, the first product of the
 * category n places after the default's, so a new four-panel view starts on
 * four kinds of field.
 */
export function defaultProductFor(catalog: MapCatalog, panel: number): FieldProduct | undefined {
  const fallback = catalog.products.find((product) => product.key === catalog.defaultProduct);
  if (panel === 0 || fallback === undefined) {
    return fallback;
  }
  const regular = catalog.products.filter((product) => product.forecastHours === undefined);
  const categories = catalog.categories.filter((category) => regular.some((product) => product.category === category));
  if (categories.length === 0) return fallback;
  const start = Math.max(0, categories.indexOf(fallback.category));
  const category = categories[(start + panel) % categories.length];
  return regular.find((product) => product.category === category) ?? fallback;
}

/** Why a panel draws no field, or null when it should. */
export type FieldBlock = "hidden" | "no-catalog" | "other-model" | "loading-models";

export interface PanelField {
  product: FieldProduct | null;
  /** The active variant for products with selectable sub-fields, else null. */
  variant: FieldVariant | null;
  /** The frame to draw; null when blocked. */
  request: FrameRequest | null;
  blocked: FieldBlock | null;
  frame: UseQueryResult<FieldFrame>;
  catalog: UseQueryResult<MapCatalog>;
}

export interface DrawnField {
  product: FieldProduct;
  key: string;
}

/** The product a panel shows: its choice (or one of its variants) when listed, else its default. */
export function productOf(catalog: MapCatalog | undefined, chosen: string | null, panel: number): FieldProduct | null {
  if (catalog === undefined) {
    return null;
  }
  return catalog.products.find((product) =>
    product.key === chosen || product.variants?.some((variant) => variant.key === chosen) === true,
  ) ?? defaultProductFor(catalog, panel) ?? null;
}

/** The active variant for `product`, or null when the product has no variants. */
export function selectedVariant(product: FieldProduct | null, chosen: string | null): FieldVariant | null {
  if (product?.variants === undefined || product.variants.length === 0) {
    return null;
  }
  return product.variants.find((variant) => variant.key === chosen)
    ?? product.variants.find((variant) => variant.key === product.key)
    ?? product.variants[0]
    ?? null;
}

/** The request key for `product`, resolving any chosen variant. */
export function variantKeyOf(product: FieldProduct, chosen: string | null): string {
  return selectedVariant(product, chosen)?.key ?? product.key;
}

/** Pressure lines are loaded by the independent Isobars layer. */
export function fieldAuxiliaries(product: FieldProduct): boolean {
  return product.wind || (product.key !== "mslp" && product.contour !== null);
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
  const variant = selectedVariant(product, chosen);
  const blocked = blockOf(visible && !live, catalog.data, context);
  const request =
    blocked === null && !catalog.isPlaceholderData && product !== null && context !== null
      ? { model: context.model.key, product: variant?.key ?? product.key, runTime: context.run, forecastHour: context.hour }
      : null;
  return { product, variant, request, blocked, frame: useFieldFrame(request), catalog };
}

/**
 * The products the visible panels draw now, without repeats (playback waits
 * for and prefetches them). A stable array while they do not change.
 */
export function useDrawnFields(): readonly DrawnField[] {
  const inventory = useMapCatalog();
  const catalog = inventory.isPlaceholderData ? undefined : inventory.data;
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
    const entries = new Map<string, DrawnField>();
    for (const [panel, entry] of chosen.split("|").entries()) {
      if (entry === "") continue;
      const choice = entry === "*" ? null : entry;
      const product = productOf(catalog, choice, panel);
      if (product === null) continue;
      const key = variantKeyOf(product, choice);
      if (!entries.has(key)) entries.set(key, { product, key });
    }
    return [...entries.values()];
  }, [catalog, fieldsAvailable, chosen]);
}

export function useDrawnProducts(): readonly FieldProduct[] {
  const fields = useDrawnFields();
  return useMemo(() => [...new Set(fields.map((entry) => entry.product))], [fields]);
}
