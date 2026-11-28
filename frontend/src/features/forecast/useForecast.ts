// The forecast context every map panel, the toolbar, and the timeline share
// (REQUEST §13): the store's choices resolved against the backend's model list
// and map catalogue. Cycles and hours come from the backend; a run's last hour
// for the map fields comes from the catalogue's per-cycle limits.

import { useMemo } from "react";
import { useModelCatalog } from "../sounding/soundingQueries";
import { addHours, resolveModelRun } from "../sounding/soundingTime";
import { minimumMapForecastHour, useMapCatalogSelection, useSelectedForecastProducts } from "../../map/catalogQuery";
import { useForecastStore, type ForecastState } from "../../stores/forecastStore";
import type { MapCatalog } from "../../types/map";
import type { ForecastModel } from "../../types/soundings";

export interface ForecastContext {
  models: readonly ForecastModel[];
  model: ForecastModel;
  run: string;
  /** The hours this run offers; for the field model, only those its cycle publishes. */
  hours: readonly number[];
  hour: number;
  validTime: string;
  /** The map catalogue's fields come from this model. */
  fieldsAvailable: boolean;
}

export type ForecastChoice = Pick<ForecastState, "modelKey" | "runTime" | "forecastHour">;

/** The last hour of `hours` at or before `wanted`, else the first. */
function closestHour(hours: readonly number[], wanted: number): number {
  return [...hours].reverse().find((hour) => hour <= wanted) ?? hours[0] ?? 0;
}

export function resolveForecast(
  models: readonly ForecastModel[],
  catalog: MapCatalog | undefined,
  choice: ForecastChoice,
  selectedProducts: readonly string[] = [],
  supported?: MapCatalog,
): ForecastContext | null {
  const fieldModel = catalog?.fieldModel ?? null;
  const model =
    models.find((item) => item.key === (choice.modelKey ?? fieldModel)) ??
    models.find((item) => item.key === fieldModel) ??
    models[0];
  if (model === undefined) {
    return null;
  }
  const run = resolveModelRun(model, choice.modelKey === null || choice.modelKey === model.key ? choice.runTime : null);
  const fieldsAvailable = catalog !== undefined && model.key === catalog.fieldModel;
  const cycle = new Date(run).getUTCHours();
  const limit = Math.min(model.maxForecastHours[cycle] ?? Infinity, fieldsAvailable ? (catalog.maxForecastHours[cycle] ?? Infinity) : Infinity);
  const productCatalog = supported?.fieldModel === model.key ? supported : fieldsAvailable ? catalog : undefined;
  const minimum = minimumMapForecastHour(productCatalog, selectedProducts);
  const allowed = model.forecastHours.filter((hour) => hour >= minimum && hour <= limit);
  if (allowed.length === 0 && minimum > 0) return null;
  const schedules = fieldsAvailable ? catalog.products.flatMap(({ key, forecastHours }) =>
    forecastHours !== undefined && selectedProducts.includes(key) ? [forecastHours] : []) : [];
  const scheduled = allowed.filter((hour) => schedules.every((schedule) => schedule.includes(hour)));
  const hours = scheduled.length > 0 ? scheduled : allowed.length > 0 ? allowed : model.forecastHours;
  const hour = closestHour(hours, choice.forecastHour ?? model.defaultForecastHour);
  return { models, model, run, hours, hour, validTime: addHours(run, hour), fieldsAvailable };
}

export type ForecastResolution =
  | { status: "loading" }
  /** The backend lists no forecast models. */
  | { status: "empty" }
  | { status: "error"; error: Error; retry: () => void; retrying: boolean }
  | { status: "ready"; context: ForecastContext };

/** The shared forecast context; the map catalogue failing only turns the fields off. */
export function useForecast(): ForecastResolution {
  const models = useModelCatalog();
  const { inventory, supported } = useMapCatalogSelection();
  const modelKey = useForecastStore((state) => state.modelKey);
  const runTime = useForecastStore((state) => state.runTime);
  const forecastHour = useForecastStore((state) => state.forecastHour);
  const selectedProducts = useSelectedForecastProducts();
  // One object while nothing it depends on changes, so effects can depend on it.
  const resolved = useMemo(
    () =>
      models.data === undefined ? null : resolveForecast(models.data.models, inventory.data, { modelKey, runTime, forecastHour }, selectedProducts, supported),
    [models.data, inventory.data, modelKey, runTime, forecastHour, selectedProducts, supported],
  );
  if (models.isError) {
    return {
      status: "error",
      error: models.error,
      retry: () => {
        void models.refetch();
      },
      retrying: models.isFetching,
    };
  }
  if (models.data === undefined) {
    return { status: "loading" };
  }
  return resolved === null ? { status: "empty" } : { status: "ready", context: resolved };
}

/** The ready context, or null. */
export function useForecastContext(): ForecastContext | null {
  const forecast = useForecast();
  return forecast.status === "ready" ? forecast.context : null;
}
