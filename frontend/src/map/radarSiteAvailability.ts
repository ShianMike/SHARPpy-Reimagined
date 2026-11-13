import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { useQueryClient, type QueryCache, type QueryKey, type UseQueryResult } from "@tanstack/react-query";
import type { RadarSite, RadarTilts } from "../types/map";
import { panelRadar } from "./panelRadar";

interface AttemptOrder { next: number; queries: WeakMap<object, { sequence: number; time: number }>; failures: WeakSet<object>; acknowledgedErrors: WeakMap<object, number>; listeners: Set<() => void>; }
interface Attempt { updatedAt: number; sequence: number; failed: boolean; }

const cacheOrders = new WeakMap<QueryCache, AttemptOrder>();

/** Track result order once per cache, including results received before a panel mounts. */
export function trackRadarCacheOrder(cache: QueryCache): AttemptOrder {
  const existing = cacheOrders.get(cache);
  if (existing !== undefined) return existing;
  const order: AttemptOrder = { next: 0, queries: new WeakMap(), failures: new WeakSet(), acknowledgedErrors: new WeakMap(), listeners: new Set() };
  cacheOrders.set(cache, order);
  cache.subscribe((event) => {
    const key = event.query.queryKey as readonly unknown[];
    const kind = key[0];
    if (kind !== "radar-site" && kind !== "radar-tilts") return;
    if (event.type === "updated") {
      const action = event.action.type;
      if (kind === "radar-site" && (action === "failed" || action === "error" || action === "setState" &&
        (event.query.state.error !== null || event.query.state.fetchFailureReason !== null || event.query.state.fetchFailureCount > 0 ||
          event.query.state.status !== "success" && event.query.state.errorUpdateCount > 0))) {
        order.failures.add(event.query);
      }
      if (action === "fetch") {
        if (kind !== "radar-site" || event.query.state.status !== "pending") return;
      } else if (action === "failed") {
        if (kind !== "radar-site") return;
        if (event.query.state.status === "pending") order.queries.set(event.query, { sequence: ++order.next, time: Date.now() });
      } else if (action === "success" || action === "error" || action === "setState") {
        // Query timestamps have millisecond precision, so preserve event order for ties.
        order.queries.set(event.query, { sequence: ++order.next, time: Date.now() });
      } else return;
    } else if (event.type !== "added" && event.type !== "removed") {
      return;
    }
    for (const notify of order.listeners) notify();
  });
  return order;
}

/** A failed active request recovers only after its own successful frame decodes. */
export function useActiveRadarFailure(
  site: string | null, key: QueryKey, result: UseQueryResult, frame: { decodedCurrent: boolean; issue: string | null },
): string | null {
  const cache = useQueryClient().getQueryCache();
  const order = trackRadarCacheOrder(cache);
  const query = site === null ? undefined : cache.find({ queryKey: key, exact: true });
  const subscribe = useCallback((notify: () => void) => {
    order.listeners.add(notify);
    return () => { order.listeners.delete(notify); };
  }, [order]);
  const snapshot = useCallback(() => query !== undefined && order.failures.has(query), [order, query]);
  const hadFailure = useSyncExternalStore(subscribe, snapshot, snapshot);
  const recovered = result.isSuccess && !result.isFetching && !result.isPlaceholderData &&
    result.failureReason === null && result.failureCount === 0 && frame.decodedCurrent && frame.issue === null;
  useEffect(() => {
    if (query === undefined) return;
    const wasFailed = order.failures.has(query);
    if (frame.issue !== null) order.failures.add(query);
    else if (recovered && query.state.data === result.data && query.state.status === "success" && query.state.fetchStatus === "idle" &&
      query.state.fetchFailureReason === null && query.state.fetchFailureCount === 0) {
      order.acknowledgedErrors.set(query, result.errorUpdateCount);
      order.failures.delete(query);
    }
    if (wasFailed !== order.failures.has(query)) for (const notify of order.listeners) notify();
  }, [order, query, frame.issue, recovered, result.data, result.errorUpdateCount]);
  const acknowledged = query === undefined ? 0 : order.acknowledgedErrors.get(query) ?? 0;
  const failed = result.isError || result.failureReason !== null || result.failureCount > 0 ||
    frame.issue !== null || !recovered && (result.errorUpdateCount > acknowledged || hadFailure);
  return site !== null && failed ? site : null;
}

const isNewer = (attempt: Attempt, previous: Attempt): boolean =>
  attempt.updatedAt > previous.updatedAt || attempt.updatedAt === previous.updatedAt && attempt.sequence > previous.sequence;

/** Completed frame attempts determine availability, including refresh errors with older data. */
function cachedUnavailable(cache: QueryCache, sites: ReadonlyMap<string, RadarSite>, product: string | null, historyTime: string | null, order: AttemptOrder): string {
  const attempts = new Map<string, Attempt>();
  const tilts = new Map<string, Attempt>();
  const products = new Map<string, string>();
  const unavailable = new Set<string>();
  for (const query of cache.getAll()) {
    const [kind, queryProduct, site, time] = query.queryKey;
    if ((kind !== "radar-site" && kind !== "radar-tilts") || typeof site !== "string" || site === "") continue;
    let effectiveProduct = products.get(site);
    if (effectiveProduct === undefined) {
      const metadata = sites.get(site);
      effectiveProduct = panelRadar({ radarScope: "site", radarSite: site, radarProduct: product, radarTilt: "0" }, null,
        metadata === undefined ? [] : [metadata]).product ?? "reflectivity";
      products.set(site, effectiveProduct);
    }
    if (queryProduct !== effectiveProduct) continue;
    const event = order.queries.get(query);
    if (kind === "radar-site" && (historyTime === null || time === historyTime && query.queryKey[4] !== "live")) {
      const { status, dataUpdatedAt, error, errorUpdatedAt, errorUpdateCount, fetchFailureReason } = query.state;
      const failed = status === "error" || status === "pending" && (error !== null || fetchFailureReason !== null || errorUpdateCount > 0);
      if (status === "pending" && !failed) continue;
      let updatedAt = failed ? errorUpdatedAt || event?.time || 0 : dataUpdatedAt;
      // An intermediate retry failure has no timestamp in the query state.
      if (status === "pending" && fetchFailureReason !== null) updatedAt = Math.max(updatedAt, event?.time ?? 0);
      const attempt = { updatedAt, sequence: event?.sequence ?? 0, failed }, previous = attempts.get(site);
      if (previous === undefined || isNewer(attempt, previous)) attempts.set(site, attempt);
    } else if (kind === "radar-tilts" && time === historyTime && query.state.status === "success") {
      const data = query.state.data as RadarTilts | undefined;
      if (data !== undefined && data.tilts.length > 0 && data.tilts.every((tilt) => tilt.status === "unavailable")) {
        tilts.set(site, { updatedAt: query.state.dataUpdatedAt, sequence: event?.sequence ?? 0, failed: true });
      }
    }
  }
  for (const [site, attempt] of attempts) if (attempt.failed) unavailable.add(site);
  for (const [site, tilt] of tilts) {
    const attempt = attempts.get(site);
    if (attempt === undefined || attempt.failed || !isNewer(attempt, tilt)) unavailable.add(site);
  }
  return JSON.stringify([...unavailable].sort());
}

/** React to cached site attempts without rerendering for unrelated query events. */
export function useUnavailableRadarSites(
  sites: readonly RadarSite[], product: string | null, historyTime: string | null, activeFailure: string | null, activeSite: string | null = null,
): ReadonlySet<string> {
  const cache = useQueryClient().getQueryCache();
  const order = trackRadarCacheOrder(cache);
  const siteIndex = useMemo(() => {
    const index = new Map<string, RadarSite>();
    for (const site of sites) if (!index.has(site.id)) index.set(site.id, site);
    return index;
  }, [sites]);
  const subscribe = useCallback((notify: () => void) => {
    order.listeners.add(notify);
    return () => { order.listeners.delete(notify); };
  }, [order]);
  const snapshot = useCallback(() => cachedUnavailable(cache, siteIndex, product, historyTime, order), [cache, siteIndex, product, historyTime, order]);
  const cached = useSyncExternalStore(subscribe, snapshot, snapshot);
  return useMemo(() => {
    const unavailable = new Set<string>(JSON.parse(cached) as string[]);
    if (activeSite !== null) unavailable.delete(activeSite);
    for (const site of sites) if (site.products?.length === 0) unavailable.add(site.id);
    if (activeFailure !== null) unavailable.add(activeFailure);
    return unavailable;
  }, [cached, sites, activeFailure, activeSite]);
}
