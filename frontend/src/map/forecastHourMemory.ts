import { hashKey, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { failureOf } from "../lib/backend/errors";
import { mapKeys } from "../lib/backend/queryOptions";
import type { DrawnField } from "./panelField";

type MemoryState = "cached" | "loading" | "failed" | "unavailable";
const EMPTY = new Map<number, MemoryState>();

export const idleForecastHourMemory = {
  snapshot: (): ReadonlyMap<number, MemoryState> => EMPTY,
  subscribe: () => () => undefined,
};

function stateOf(client: QueryClient, keys: readonly QueryKey[]): MemoryState | undefined {
  let ready = true;
  let error: "failed" | "unavailable" | undefined;
  for (const key of keys) {
    const state = client.getQueryState(key);
    if (state?.fetchStatus === "fetching" || state?.fetchStatus === "paused") return "loading";
    if (state?.status === "error" && error !== "failed") {
      error = failureOf(state.error)?.code === "FIELD_UNAVAILABLE" ? "unavailable" : "failed";
    }
    ready &&= state?.status === "success" && state.data !== undefined;
  }
  return error ?? (ready ? "cached" : undefined);
}

/** Track only the in-memory cache rows used by the forecast-hour picker and status. */
export function forecastHourMemory(client: QueryClient, model: string, run: string,
  hours: readonly number[], products: readonly DrawnField[], minimum: number) {
  if (products.length === 0) return idleForecastHourMemory;
  const rows: { hour: number; keys: QueryKey[] }[] = [];
  const rowByQuery = new Map<string, typeof rows[number]>();
  for (const hour of hours) {
    if (hour < minimum) continue;
    const keys = products.flatMap(({ product, key }): QueryKey[] => [
      mapKeys.field(key, run, hour, model),
      ...(product.contour !== null || product.wind ? [mapKeys.contours(product.key, run, hour, model)] : []),
    ]);
    const row = { hour, keys };
    for (const key of keys) rowByQuery.set(hashKey(key), row);
    rows.push(row);
  }
  const readAll = () => {
    const states = new Map<number, MemoryState>();
    for (const { hour, keys } of rows) {
      const state = stateOf(client, keys);
      if (state !== undefined) states.set(hour, state);
    }
    return states;
  };
  let states = readAll();
  return {
    snapshot: (): ReadonlyMap<number, MemoryState> => states,
    subscribe: (changed: () => void) => {
      const release = client.getQueryCache().subscribe((event) => {
        if (event.type !== "updated" && event.type !== "removed") return;
        const row = rowByQuery.get(event.query.queryHash);
        if (row === undefined) return;
        const { hour, keys } = row;
        const next = stateOf(client, keys);
        if (states.get(hour) === next) return;
        states = new Map(states);
        if (next === undefined) states.delete(hour);
        else states.set(hour, next);
        changed();
      });
      // Bridge the gap between the render's first read and React's subscription.
      const current = readAll();
      if (current.size !== states.size || [...current].some(([hour, state]) => states.get(hour) !== state)) {
        states = current;
        changed();
      }
      return release;
    },
  };
}
