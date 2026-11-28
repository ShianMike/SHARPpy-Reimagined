import { useQueries, useQuery, useQueryClient, type QueryObserverOptions } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import type { BackendClient } from "../../lib/backend/client";
import { latestSpcOutlookQueryOptions, SPC_REFRESH_MS, wpcOutlookQueryOptions } from "../../map/outlookQueries";
import {
  OUTLOOK_DAYS,
  OUTLOOK_KEYS,
  WPC_WINTER_PRODUCTS,
  type OutlookDay,
  type OutlookProductKey,
  type SpcOutlook,
  type WpcProduct,
} from "../../types/map";

export const SPC_PRODUCTS_BY_DAY: Readonly<Record<OutlookDay, readonly OutlookProductKey[]>> = {
  1: OUTLOOK_KEYS,
  2: OUTLOOK_KEYS,
  3: ["cat", "prob"],
};

type BriefingRequest = { day: OutlookDay } & (
  { agency: "spc"; product: OutlookProductKey } | { agency: "wpc"; product: WpcProduct }
);

const BRIEFINGS: readonly (readonly BriefingRequest[])[] = [
  OUTLOOK_DAYS.flatMap((day) =>
    SPC_PRODUCTS_BY_DAY[day].map((product): BriefingRequest => ({ agency: "spc", day, product }))),
  OUTLOOK_DAYS.map((day): BriefingRequest => ({ agency: "wpc", day, product: "ero" })),
  [
    ...OUTLOOK_DAYS.map((day): BriefingRequest => ({ agency: "wpc", day, product: "winter-overall" })),
    ...OUTLOOK_DAYS.flatMap((day) =>
      WPC_WINTER_PRODUCTS.slice(1).map((product): BriefingRequest => ({ agency: "wpc", day, product }))),
  ],
];

function briefingQueryOptions(client: BackendClient, request: BriefingRequest): QueryObserverOptions<SpcOutlook> {
  return request.agency === "spc"
    ? latestSpcOutlookQueryOptions(client, request.day, request.product)
    : wpcOutlookQueryOptions(client, "", request.day, request.product);
}

export function useBriefingOutlook(selection: BriefingRequest) {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const groups = useMemo(() => BRIEFINGS.map((group) => group.map((request) => briefingQueryOptions(client, request))), [client]);

  // Keep queries observed across tab changes so switching away cannot cancel a
  // shared in-flight request or discard preloaded guidance while Overview is open.
  const observers = useMemo(() => groups.flat().map((options) => ({ ...options, enabled: false })), [groups]);
  useQueries({
    queries: observers,
    combine: () => null,
  });
  const selectedOptions = briefingQueryOptions(client, selection);
  const selected = useQuery({
    ...selectedOptions,
    refetchInterval: SPC_REFRESH_MS,
    refetchOnWindowFocus: "always",
  });

  useEffect(() => {
    const lifetime = new AbortController();
    // Each family can reuse its discussion downloads across days or hazards.
    for (const group of groups) {
      void (async () => {
        for (const options of group) {
          if (lifetime.signal.aborted) break;
          // Keep warming the other choices after a failure. The selected
          // outlook can retry normally when opened.
          await queryClient.query({ ...options, retry: false }).catch(() => undefined);
        }
      })();
    }
    return () => { lifetime.abort(); };
  }, [groups, queryClient]);

  return selected;
}
