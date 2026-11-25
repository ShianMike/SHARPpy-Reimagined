import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import type { BackendClient } from "../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import { useForecastStore } from "../stores/forecastStore";
import { useModelCatalog } from "../features/sounding/soundingQueries";
import { resolveModelRun } from "../features/sounding/soundingTime";
import type { MapCatalog } from "../types/map";

function usesRunInventory(model: string): boolean {
  return model === "hrrr" || model === "rap" || model === "rdps" || model === "hrdps" || model === "rrfs-a" || model === "refs" || model.startsWith("refs-")
    || model === "nam-3km-conus" || model === "gfs" || model === "gdps" || model === "aigfs"
    || model === "openmeteo-icon-global" || model === "openmeteo-ecmwf-ifs025" || model === "ecmwf-ifs-9km" || model.startsWith("hrw-");
}

/** Share the visible and prepared frame's product inventory. */
export function mapCatalogQueryOptions(client: BackendClient, model: string, run?: string, hour?: number) {
  const dynamic = usesRunInventory(model);
  const runTime = dynamic ? run : undefined;
  const forecastHour = dynamic ? hour : undefined;
  return {
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.catalog(model, runTime, forecastHour),
    queryFn: () => client.getMapCatalog(model, runTime, forecastHour),
    staleTime: dynamic ? 10 * 60_000 : Number.POSITIVE_INFINITY,
  } as const;
}

export function useMapCatalog(): UseQueryResult<MapCatalog> {
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
  const hour = hours?.findLast((value) => value <= limit && value <= (hourChoice ?? selected?.defaultForecastHour ?? 0))
    ?? hours?.find((value) => value <= limit);
  return useQuery({
    ...mapCatalogQueryOptions(client, model, run, hour),
    // An hour-specific inventory must not remove the palette while it loads.
    // Never carry products across a different model or initialization.
    placeholderData: (previous, query) => query?.queryKey[2] === model && query.queryKey[3] === run ? previous : undefined,
    enabled: !dynamic || (run !== undefined && hour !== undefined),
  });
}
