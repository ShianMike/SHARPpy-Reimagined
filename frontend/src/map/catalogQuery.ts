import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useShallow } from "zustand/react/shallow";
import { useBackendClient } from "../lib/backend/BackendProvider";
import type { BackendClient } from "../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import { useForecastStore } from "../stores/forecastStore";
import { panelLayers, panelProduct, useMapStore } from "../stores/mapStore";
import { useModelCatalog } from "../features/sounding/soundingQueries";
import { isNsslMpas, resolveModelRun } from "../features/sounding/soundingTime";
import type { MapCatalog } from "../types/map";
import { nameMapParameter } from "./mapParameters";

function usesRunInventory(model: string): boolean {
  return model === "hrrr" || model === "rap" || model === "rdps" || model === "hrdps" || model === "rrfs-a" || model === "refs" || model.startsWith("refs-")
    || model === "nam-3km-conus" || model === "gfs" || model === "gdps" || model === "aigfs"
    || model === "openmeteo-icon-global" || model === "openmeteo-ecmwf-ifs025" || model === "ecmwf-ifs-9km" || model.startsWith("hrw-")
    || isNsslMpas(model);
}

export function useSelectedForecastProducts(): readonly string[] {
  return useMapStore(useShallow((state) => Array.from({ length: state.panelLayout }, (_, panel) =>
    panelLayers(state, panel)["model-field"].visible ? panelProduct(state, panel) ?? "" : "",
  )));
}

export function minimumMapForecastHour(catalog: MapCatalog | undefined, selectedProducts: readonly string[]): number {
  if (catalog === undefined) return 0;
  const firstMapHour = isNsslMpas(catalog.fieldModel)
    ? Math.min(...catalog.products.map((product) => product.minimumForecastHour)) : 0;
  return Math.max(firstMapHour, ...catalog.products.flatMap((product) =>
    product.palette.ptypeLabels !== undefined && selectedProducts.includes(product.key)
      ? [product.minimumForecastHour] : []));
}

/** Share the visible and prepared frame's product inventory. */
export function mapCatalogQueryOptions(client: BackendClient, model: string, run?: string, hour?: number) {
  const dynamic = usesRunInventory(model);
  const runTime = dynamic ? run : undefined;
  const forecastHour = dynamic ? hour : undefined;
  return {
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.catalog(model, runTime, forecastHour),
    queryFn: async (): Promise<MapCatalog> => {
      const catalog = await client.getMapCatalog(model, runTime, forecastHour);
      return { ...catalog, products: catalog.products.map((product) => nameMapParameter(product, catalog.fieldModel)) };
    },
    staleTime: runTime === undefined ? Number.POSITIVE_INFINITY : 10 * 60_000,
  } as const;
}

export function useSupportedMapCatalog(model: string, enabled = true) {
  const client = useBackendClient();
  return useQuery({
    ...mapCatalogQueryOptions(client, model),
    enabled: enabled && usesRunInventory(model),
  });
}

export function useMapCatalogSelection() {
  const client = useBackendClient();
  const requestedModel = useForecastStore((state) => state.modelKey) ?? "hrrr";
  const models = useModelCatalog().data;
  const selected = models?.models.find((item) => item.key === requestedModel) ?? models?.models[0];
  const model = selected?.key ?? "hrrr";
  const runChoice = useForecastStore((state) => state.runTime);
  const hourChoice = useForecastStore((state) => state.forecastHour);
  const dynamic = usesRunInventory(model);
  const run = dynamic && selected !== undefined ? resolveModelRun(selected, model === requestedModel ? runChoice : null) : undefined;
  const limit = run === undefined ? 0 : selected?.maxForecastHours[new Date(run).getUTCHours()] ?? 0;
  const hours = run === undefined ? undefined : selected?.forecastHours;
  const selectedProducts = useSelectedForecastProducts();
  const needsSupportedCatalog = isNsslMpas(model) || selectedProducts.some((key) => key.endsWith("-ptype"));
  // The hour-specific inventory omits products before their first valid frame.
  const supported = useSupportedMapCatalog(model, needsSupportedCatalog && selected !== undefined);
  const minimum = minimumMapForecastHour(supported.data, selectedProducts);
  const hour = hours?.findLast((value) => value >= minimum && value <= limit && value <= (hourChoice ?? selected?.defaultForecastHour ?? 0))
    ?? hours?.find((value) => value >= minimum && value <= limit);
  const inventory = useQuery({
    ...mapCatalogQueryOptions(client, model, run, hour),
    // An hour-specific inventory must not remove the palette while it loads.
    // Never carry products across a different model or initialization.
    placeholderData: (previous, query) => query?.queryKey[2] === model && query.queryKey[3] === run ? previous : undefined,
    enabled: !dynamic || (run !== undefined && hour !== undefined && (!needsSupportedCatalog || !supported.isPending)),
  });
  return { inventory, supported: supported.data };
}

export function useMapCatalog(): UseQueryResult<MapCatalog> {
  return useMapCatalogSelection().inventory;
}
