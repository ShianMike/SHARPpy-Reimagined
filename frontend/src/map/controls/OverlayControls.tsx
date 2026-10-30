// The radar, satellite, and SPC rows' own controls (Phases 5 and 6): the radar
// scope (mosaic or one antenna), the product, the site picker in single-site
// scope, and the frame's age and source; the satellite channel and its scan
// provenance; and for each SPC layer, the outlook found for the forecast valid
// time, honest about none and about one that does not cover it.

import type { ReactNode } from "react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { compactUtc, formatUtc } from "../../features/sounding/soundingTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { OverlayErrorNotice } from "./OverlayErrorNotice";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { OUTLOOK_KEYS, SATELLITE_CHANNELS, type OutlookKey } from "../../types/map";
import { RadarTiltSelect } from "../../features/tracking/RadarTiltSelect";
import { useMapCatalog, usePanelRadar, usePlaceLabels, useRadarFrame, useRadarSiteFrame, useRadarSites, useSatelliteFrame, useSpcOutlooks } from "../mapQueries";
import { domainPath } from "../panelScene";
import { isAustralianRadarSite } from "../panelRadar";
import styles from "./LayerPanel.module.css";

/** The satellite row's own controls: the channel, and the scan's own provenance. */
export function SatelliteControls(): ReactNode {
  const validTime = useWeatherTime();
  const channel = useMapStore((state) => panelWeather(state).satelliteChannel);
  const frame = useSatelliteFrame(channel, validTime, true);
  return (
    <div className={styles.extra}>
      <SourceSelect label="Channel" value={channel}
        options={SATELLITE_CHANNELS.map((key) => ({ value: key, label: key === "infrared" ? "Clean IR (10.3 µm)" : "Visible (0.64 µm)" }))}
        onChange={useMapStore.getState().setSatelliteChannel} />
      {validTime === null ? (
        <p className={styles.hint}>Choose a time to view GOES imagery.</p>
      ) : frame.isError ? (
        <>
          <SoundingErrorNotice
            error={frame.error}
            operation="get_satellite_frame"
            onRetry={() => {
              void frame.refetch();
            }}
            busy={frame.isFetching}
            testId="satellite-error"
          />
          {frame.data === undefined ? null : <p className={styles.warning}>Showing the last loaded scan, {formatUtc(frame.data.validTime)}.</p>}
        </>
      ) : frame.data === undefined ? (
        <p className={styles.hint}>Loading the GOES frame</p>
      ) : (
        <p className={styles.hint} data-testid="satellite-shown">
          {`${frame.data.label}, scan ${formatUtc(frame.data.validTime)}${
            frame.data.validTime === frame.data.requestedTime
              ? ""
              : ` (${frame.data.validTime > frame.data.requestedTime ? "+" : ""}${String(
                  Math.round((Date.parse(frame.data.validTime) - Date.parse(frame.data.requestedTime)) / 60000),
                )} min from requested)`
          }, ${String(Math.round(frame.data.coverageFraction * 100))}% view coverage at ${String(
            Math.round(frame.data.nativeResolutionKm * 10) / 10,
          )} km. ${frame.data.attribution}.`}
        </p>
      )}
    </div>
  );
}

export function RadarControls(): ReactNode {
  const validTime = useWeatherTime();
  const catalog = useMapCatalog().data;
  const { radarScope: scope, radarSite: pinnedSite } = useMapStore((state) => panelWeather(state));
  const sites = useRadarSites(true);
  const { site: effectiveSite, product, tilt, tiltable, supportedProducts } = usePanelRadar();
  const mosaic = useRadarFrame(product, scope === "mosaic", validTime);
  const siteFrame = useRadarSiteFrame(product, effectiveSite, scope === "site", validTime, tilt);
  const radar = scope === "site" ? siteFrame : mosaic;
  const products = (scope === "site" ? (catalog?.siteProducts ?? []) : (catalog?.radarProducts ?? []))
    .filter((item) => scope !== "site" || supportedProducts === undefined || supportedProducts.includes(item.key));
  const value = product ?? radar.data?.product ?? products[0]?.key ?? "";
  const shownSite: string =
    scope === "site" && radar.data !== undefined && "siteId" in radar.data && typeof radar.data.siteId === "string"
      ? ` from ${radar.data.siteId}`
      : "";
  const shown =
    radar.data === undefined
      ? "Radar"
      : `${radar.data.label}${shownSite}${
          radar.data.validTime === null ? "" : `, valid ${formatUtc(radar.data.validTime)}`
        }${radar.data.retrievedAt === null ? "" : `, retrieved ${formatUtc(radar.data.retrievedAt)}`}`;
  return (
    <div className={styles.extra}>
      <SourceSelect<"mosaic" | "site"> label="Radar" value={scope}
        options={[{ value: "mosaic", label: "CONUS mosaic" }, { value: "site", label: "Single site" }]}
        onChange={useMapStore.getState().setRadarScope} />
      {scope === "site" ? (
        sites.data === undefined ? (
          <p className={styles.hint}>Loading the radar sites</p>
        ) : (
          <SourceSelect label="Site" value={pinnedSite ?? ""}
            options={[{ value: "", label: "Nearest to map centre" }, ...sites.data.sites.map((site) => ({ value: site.id, label: site.name === undefined ? site.id : `${site.id} · ${site.name}`, ...(site.country === "AU" ? { detail: "Australia" } : {}) }))]}
            onChange={(value) => {
              const state = useMapStore.getState();
              state.setRadarSite(value === "" ? null : value);
              const selectedSite = sites.data.sites.find((site) => site.id === value);
              if (selectedSite !== undefined) state.requestFocus(selectedSite.id, { latitude: selectedSite.latitude, longitude: selectedSite.longitude });
            }} />
        )
      ) : null}
      {products.length === 0 ? null : (
        <SourceSelect label="Product" value={value}
          options={products.map((item) => ({ value: item.key, label: item.label }))}
          onChange={useMapStore.getState().setRadarProduct} />
      )}
      {scope === "site" && (tiltable ? <RadarTiltSelect label="Tilt" site={effectiveSite} product={value} tilt={tilt} angle={siteFrame.data?.elevationAngle} />
        : isAustralianRadarSite(effectiveSite) ? <SourceSelect label="Tilt" value="0" options={[{ value: "0", label: "Lowest available" }]} disabled onChange={useMapStore.getState().setRadarTilt} /> : null)}
      {radar.isError ? (
        <>
          <OverlayErrorNotice
            error={radar.error}
            operation={scope === "site" ? "get_radar_site_frame" : "get_radar_frame"}
            onRetry={() => {
              void radar.refetch();
            }}
            busy={radar.isFetching}
            testId="radar-error"
          />
          {radar.data === undefined ? null : <p className={styles.warning}>{`Refresh failed. Showing ${shown}.`}</p>}
        </>
      ) : radar.data === undefined ? (
        <p className={styles.hint}>Loading the radar</p>
      ) : (
        <p className={styles.hint} data-testid="radar-shown">
          {`${shown}. ${
            scope === "site"
              ? "One antenna's own imagery at its native resolution."
              : "High-resolution MRMS mosaic."
          } ${radar.data.attribution}.`}
        </p>
      )}
    </div>
  );
}

/** Place names say something only when they could not be loaded. */
export function PlaceStatus(): ReactNode {
  const places = usePlaceLabels(true);
  if (!places.isError) {
    return null;
  }
  return (
    <SoundingErrorNotice
      error={places.error}
      operation="get_place_labels"
      onRetry={() => {
        void places.refetch();
      }}
      busy={places.isFetching}
      testId="places-error"
    />
  );
}

/** What the domain outline is: the grid's perimeter, its bounds, or nothing for a global model. */
export function DomainStatus(): ReactNode {
  const model = useForecastContext()?.model;
  if (model === undefined) {
    return null;
  }
  const text =
    domainPath(model) === null
      ? `${model.label} covers the globe, so there is no outline to draw.`
      : model.outline.length >= 3
        ? `The ${model.label} grid's perimeter (${model.domain}).`
        : `The bounds of the ${model.label} grid (${model.domain}); its exact outline is not known.`;
  return <p className={styles.hint}>{text}</p>;
}

/** The state of one SPC layer of a panel, for the forecast valid time. */
export function OutlookStatus({ panel, product }: { panel: number; product: OutlookKey }): ReactNode {
  const validTime = useWeatherTime();
  const visible = useMapStore((state) => panelLayers(state, panel)[`spc-${product}` as const].visible);
  const queries = useSpcOutlooks(
    OUTLOOK_KEYS.map((key) => key === product && visible),
    validTime,
  );
  const query = queries[OUTLOOK_KEYS.indexOf(product)];
  if (validTime === null) {
    return <p className={styles.hint}>Choose a time to view SPC outlooks.</p>;
  }
  if (query === undefined) {
    return null;
  }
  if (query.isError) {
    return (
      <SoundingErrorNotice
        error={query.error}
        operation="get_spc_outlook"
        onRetry={() => {
          void query.refetch();
        }}
        busy={query.isFetching}
        testId={`outlook-error-${product}`}
      />
    );
  }
  const outlook = query.data?.outlook;
  if (query.data === undefined) {
    return <p className={styles.hint}>Loading the SPC outlook</p>;
  }
  if (outlook === null || outlook === undefined) {
    return (
      <p className={styles.hint} data-testid={`outlook-none-${product}`}>
        {`SPC published no outlook of this kind for ${compactUtc(validTime)}.`}
      </p>
    );
  }
  return (
    <p className={outlook.covers ? styles.hint : styles.warning} data-testid={`outlook-shown-${product}`}>
      {outlook.covers
        ? `${outlook.subtitle}. ${outlook.attribution}.`
        : `No SPC outlook covers ${compactUtc(validTime)}. Nearest issuance: ${outlook.subtitle}.`}
    </p>
  );
}
