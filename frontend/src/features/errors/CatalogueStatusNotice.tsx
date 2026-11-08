// The single owner of the catalogue's four non-success states (DR6-2,
// DESIGN-frontend F8 States table): "Loading stations" (quiet, no spinner),
// "The station catalogue is empty", the seed-fallback warning, and a
// BackendErrorNotice with the C6 action for the failure. Nothing renders once
// the bundled catalogue is loaded. WeatherMap portals it into the overlay's
// centre column above MapNotices; ReducedRenderingView renders it above
// StationList.

import { useState, type ReactNode } from "react";
import { Notice } from "../../components/Notice";
import { Action } from "../../ui/Controls";
import { failureOf, transportFailed } from "../../lib/backend/errors";
import { useKnownHealth } from "../../lib/backend/useKnownHealth";
import { useRestartBackend } from "../../lib/backend/useRestartBackend";
import { useStationCatalog } from "../../stations/stationQueries";
import { BackendErrorNotice } from "./BackendErrorNotice";

export const CATALOGUE_LOADING = "Loading stations";
export const CATALOGUE_EMPTY = "The station catalogue is empty";

export function seedFallbackText(count: number): string {
  return `Limited station list · ${String(count)} stations`;
}

export interface CatalogueStatusNoticeProps {
  surface: "floating" | "flat";
}

export function CatalogueStatusNotice({ surface }: CatalogueStatusNoticeProps): ReactNode {
  const catalog = useStationCatalog();
  const health = useKnownHealth();
  const { restart, pending } = useRestartBackend();
  const [lastError, setLastError] = useState(catalog.error);
  if (catalog.isError && lastError !== catalog.error) setLastError(catalog.error);
  if (catalog.isSuccess && lastError !== null) setLastError(null);
  // A first-load retry clears the query's error while fetching. Keep its recovery
  // control mounted so the chosen action can show progress until a result arrives.
  const error = catalog.error ?? (catalog.isFetching ? lastError : null);

  if (catalog.status === "error" || error !== null) {
    return (
      <BackendErrorNotice
        error={failureOf(error) ?? transportFailed("get_station_catalog")}
        onRetry={() => {
          void catalog.refetch();
        }}
        onRestart={restart}
        busy={pending || catalog.isFetching}
        logFile={health?.app.logFile ?? null}
        surface={surface}
        testId="catalogue-error"
      />
    );
  }
  if (catalog.status === "pending") {
    return <Notice tone="neutral" surface={surface} title={CATALOGUE_LOADING} testId="catalogue-loading" />;
  }
  if (catalog.data.stations.length === 0) {
    return <Notice tone="info" surface={surface} title={CATALOGUE_EMPTY} testId="catalogue-empty" />;
  }
  if (catalog.data.origin === "seedFallback") {
    return (
      <Notice
        tone="warning"
        surface={surface}
        title={seedFallbackText(catalog.data.stations.length)}
        message="Using the backup list. Reload stations to restore the full catalogue."
        actions={<Action disabled={catalog.isFetching} onClick={() => { void catalog.refetch(); }}>Reload stations</Action>}
        testId="catalogue-seed-fallback"
      />
    );
  }
  return null;
}
