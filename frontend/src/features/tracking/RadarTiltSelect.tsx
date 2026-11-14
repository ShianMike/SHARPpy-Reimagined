import { useQuery } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import { useEffect } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { radarTiltQuery } from "../../map/radarTiltQueries";
import { isFixedScanRadarSite } from "../../map/panelRadar";
import { useRadarSites } from "../../map/mapQueries";
import { liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import { RADAR_TILTS, type RadarTilt } from "../../types/map";
import { SourceSelect } from "../sounding/SourceFields";
import { SoundingErrorNotice } from "../sounding/SoundingErrorNotice";

/** Both pickers offer confirmed cuts for this site, product and viewing time. */
export function RadarTiltSelect({ label, site, product, tilt, angle, toolbarIcon }: {
  label: string; site: string | null; product: string; tilt: RadarTilt; angle?: number | null | undefined; toolbarIcon?: LucideIcon;
}) {
  const client = useBackendClient();
  const panel = useMapStore((state) => state.activePanel);
  const selectedTime = useLiveTrackingStore((state) => state.selectedTime);
  const time = selectedTime === null ? null : liveTimestamp(selectedTime);
  const metadata = useRadarSites(false).data?.sites.find((entry) => entry.id === site);
  const fixedScan = isFixedScanRadarSite(site, metadata);
  const query = useQuery({ ...radarTiltQuery(client, site ?? "", product, time), enabled: site !== null && !fixedScan, refetchInterval: 45_000 });
  const available = query.isError || fixedScan ? [] : RADAR_TILTS.filter((cut) => query.data?.tilts.some((item) => item.tilt === cut.value && item.status === "available"));
  const first = available[0]?.value;
  useEffect(() => {
    const state = useMapStore.getState();
    const weather = panelWeather(state);
    if (state.view === "live" && state.activePanel === panel && weather.radarScope === "site"
      && (weather.radarSite === null || weather.radarSite === site) && (weather.radarProduct ?? "reflectivity") === product
      && weather.radarTilt === tilt && first !== undefined && query.data?.tilts.find((item) => item.tilt === tilt)?.status !== "available") state.setRadarTilt(first);
  }, [first, panel, product, site, tilt, query.data]);
  const empty = fixedScan ? (/^D[A-Z]{3}$/.test(site ?? "") ? "Varies by direction" : "Lowest available") : query.isError ? "Elevations unavailable" : query.data === undefined ? "Checking tilts…"
    : query.data.tilts.some((cut) => cut.status === "unknown") ? "Could not verify tilts" : "No recent scans";
  return <><SourceSelect label={label} value={tilt} options={available.length === 0 ? [{ value: tilt, label: empty }] : available.map((cut) => ({ ...cut, label: cut.value === tilt && angle !== null && angle !== undefined ? `${angle.toFixed(1)}°` : cut.label }))}
    disabled={available.length === 0} onChange={useMapStore.getState().setRadarTilt} {...(toolbarIcon === undefined ? {} : { toolbarIcon, popup: "list" })} />
    {query.isError && toolbarIcon === undefined ? <SoundingErrorNotice error={query.error} operation="get_radar_tilts"
      onRetry={() => { void query.refetch(); }} busy={query.isFetching} testId="radar-tilts-error" /> : null}</>;
}
