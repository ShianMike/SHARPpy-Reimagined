import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import { useForecastStore } from "../stores/forecastStore";
import { useModelCatalog } from "../features/sounding/soundingQueries";
import { resolveModelRun } from "../features/sounding/soundingTime";
import type { MapCatalog } from "../types/map";

export function useMapCatalog(): UseQueryResult<MapCatalog> {
  const client = useBackendClient();
  const model = useForecastStore((state) => state.modelKey) ?? "hrrr";
  const models = useModelCatalog().data;
  const runChoice = useForecastStore((state) => state.runTime);
  const hourChoice = useForecastStore((state) => state.forecastHour);
  const dynamic = model === "gfs" || model === "aigfs" || model === "openmeteo-ecmwf-ifs025" || model === "ecmwf-ifs-9km" || model.startsWith("hrw-");
  const selected = models?.models.find((item) => item.key === model);
  const run = dynamic && selected !== undefined ? resolveModelRun(selected, runChoice) : undefined;
  const limit = run === undefined ? 0 : selected?.maxForecastHours[new Date(run).getUTCHours()] ?? 0;
  const hour = run === undefined ? undefined : selected?.forecastHours.findLast((value) => value <= limit && value <= (hourChoice ?? selected.defaultForecastHour));
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.catalog(model, run, hour),
    queryFn: () => client.getMapCatalog(model, run, hour),
    enabled: !dynamic || (run !== undefined && hour !== undefined),
    staleTime: dynamic ? 10 * 60_000 : Number.POSITIVE_INFINITY,
  });
}
