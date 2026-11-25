import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import type { BackendClient } from "../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import type { OutlookDay, OutlookProductKey, SpcOutlook, WpcOutlook, WpcOutlookDayMode, WpcProduct } from "../types/map";

export const SPC_REFRESH_MS = 5 * 60_000;

/** Keep each day's latest issuance separate from the map's time-based outlooks. */
export function useLatestSpcOutlook(day: OutlookDay, product: OutlookProductKey, enabled = true): UseQueryResult<SpcOutlook> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: ["spc-latest", day, product],
    queryFn: ({ signal }) => client.getSpcOutlook(product, `${new Date(Date.now()).toISOString().slice(0, 19)}Z`, signal, day),
    enabled,
    staleTime: SPC_REFRESH_MS,
    gcTime: 3 * SPC_REFRESH_MS,
    refetchInterval: SPC_REFRESH_MS,
    refetchOnWindowFocus: "always",
  });
}

export function wpcOutlookQueryOptions(
  client: BackendClient,
  validTime: string,
  dayMode: WpcOutlookDayMode = "auto",
  product: WpcProduct = "ero",
) {
  const queryKey: readonly ["wpc-outlook", WpcProduct, string] | readonly ["wpc-latest", WpcProduct, OutlookDay] =
    dayMode === "auto" ? mapKeys.wpcOutlook(validTime, product) : mapKeys.wpcLatest(dayMode, product);
  return {
    ...BASE_QUERY_OPTIONS,
    queryKey,
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      dayMode === "auto"
        ? client.getWpcOutlook(validTime, signal, undefined, product)
        : client.getWpcOutlook(`${new Date(Date.now()).toISOString().slice(0, 19)}Z`, signal, dayMode, product),
    staleTime: SPC_REFRESH_MS,
    gcTime: dayMode === "auto" ? Number.POSITIVE_INFINITY : 3 * SPC_REFRESH_MS,
  };
}

export function useWpcOutlook(
  enabled: boolean,
  validTime: string | null,
  dayMode: WpcOutlookDayMode = "auto",
  product: WpcProduct = "ero",
): UseQueryResult<WpcOutlook> {
  const client = useBackendClient();
  const time = validTime ?? "";
  return useQuery({
    ...wpcOutlookQueryOptions(client, time, dayMode, product),
    enabled: enabled && (dayMode !== "auto" || time !== ""),
    refetchInterval: SPC_REFRESH_MS,
  });
}

/** Latest WPC Excessive Rainfall or Winter Weather Outlook & discussion by Day 1/2/3. */
export function useLatestWpcOutlook(day: OutlookDay, enabled = true, product: WpcProduct = "ero"): UseQueryResult<WpcOutlook> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.wpcLatest(day, product),
    queryFn: ({ signal }) => client.getWpcOutlook(`${new Date(Date.now()).toISOString().slice(0, 19)}Z`, signal, day, product),
    enabled,
    staleTime: SPC_REFRESH_MS,
    gcTime: 3 * SPC_REFRESH_MS,
    refetchInterval: SPC_REFRESH_MS,
    refetchOnWindowFocus: "always",
  });
}
