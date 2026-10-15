// Station availability (Phase 2): the bundled catalogue marked against the
// stations UWyo lists for one observation time. Listed bundled stations are
// "listed", the rest "notListed", and listed stations the catalogue lacks
// join it. Pure; the result is memoized per catalogue and report, so every
// view shares one array.

import type { Station, StationCatalog } from "../types/contracts";
import type { AvailabilityReport } from "../types/map";

const merged = new WeakMap<StationCatalog, WeakMap<AvailabilityReport, StationCatalog>>();

const byId = (a: Station, b: Station): number => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function withAvailability(catalog: StationCatalog, report: AvailabilityReport): StationCatalog {
  let reports = merged.get(catalog);
  if (reports === undefined) {
    reports = new WeakMap();
    merged.set(catalog, reports);
  }
  const cached = reports.get(report);
  if (cached !== undefined) {
    return cached;
  }
  const listed = new Set(report.listedIds);
  const known = new Set(catalog.stations.map((station) => station.id));
  const stations: Station[] = [
    ...catalog.stations.map((station) => ({ ...station, availability: listed.has(station.id) ? ("listed" as const) : ("notListed" as const) })),
    ...report.extraStations.filter((station) => !known.has(station.id)).map((station) => ({ ...station, availability: "listed" as const })),
  ].sort(byId);
  const result: StationCatalog = {
    ...catalog,
    stations,
    availabilityBasis: { kind: "listedAt", observationTime: report.observationTime },
    droppedCount: catalog.droppedCount + report.droppedCount,
  };
  reports.set(report, result);
  return result;
}

const pad = (value: number): string => String(value).padStart(2, "0");

/**
 * The newest `count` 00Z and 12Z release times that have had two hours to
 * reach the UWyo archive, newest first, as RFC 3339 UTC.
 */
export function recentReleases(now: Date, count: number): string[] {
  const settled = new Date(now.getTime() - 2 * 3_600_000);
  const start = Date.UTC(settled.getUTCFullYear(), settled.getUTCMonth(), settled.getUTCDate(), settled.getUTCHours() >= 12 ? 12 : 0);
  return Array.from({ length: count }, (_, i) => {
    const moment = new Date(start - i * 12 * 3_600_000);
    return `${String(moment.getUTCFullYear())}-${pad(moment.getUTCMonth() + 1)}-${pad(moment.getUTCDate())}T${pad(moment.getUTCHours())}:00:00Z`;
  });
}
