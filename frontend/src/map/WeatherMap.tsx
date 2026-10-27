// WeatherMap (DESIGN-frontend F4) only wires: it lays out one, two, or four
// map panels (REQUEST §14), renders the tooltips, and portals the map toolbar,
// the notices, and the layer panel into the Shell's overlay slots. Each panel
// owns its runtime and data (panels/MapPanel.tsx); panel 0 is the primary map.
// The default export is for React.lazy: MapLibre and deck.gl load in this chunk
// after the shell paints. The inner view is keyed by the profile's antialias
// value and graphicsStore.mapAttempt, so a new WebGL context, Reload map, and
// Try the map again each rebuild the maps; the camera comes back from the store.

import "maplibre-gl/dist/maplibre-gl.css";
import "./maplibre-overrides.css";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal, flushSync } from "react-dom";
import { useMapOverlaySlots } from "../app/MapOverlaySlots";
import { VisuallyHidden } from "../components/VisuallyHidden";
import { CatalogueStatusNotice } from "../features/errors/CatalogueStatusNotice";
import { profileSettings } from "../lib/graphics/profileSettings";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useMapStore, type PanelLayout } from "../stores/mapStore";
import { useStationCatalog } from "../stations/stationQueries";
import type { Station, StationAvailability } from "../types/contracts";
import { LayerPanel } from "./controls/LayerPanel";
import { MapToolbar } from "./controls/MapToolbar";
import { OutlookTooltip } from "./hover/OutlookTooltip";
import { ReportTooltip } from "./hover/ReportTooltip";
import { StationTooltip } from "./hover/StationTooltip";
import { MapNotices } from "./notices/MapNotices";
import { MAP_HINT_ID, MapPanel } from "./panels/MapPanel";
import type { MapRuntime } from "./runtime/MapRuntime";
import { usePlayback } from "./usePlayback";
import styles from "./WeatherMap.module.css";

const NO_STATIONS: readonly Station[] = [];

function MapView(): ReactNode {
  const [runtime, setRuntime] = useState<MapRuntime | null>(null);
  const slots = useMapOverlaySlots();
  const layout = useMapStore((state) => state.panelLayout);
  const live = useMapStore((state) => state.view === "live");
  const grid = useRef<HTMLDivElement & { startViewTransition?: Document["startViewTransition"] }>(null);
  const requestedLayout = useRef<PanelLayout | null>(null);
  const catalog = useStationCatalog();
  const stations = catalog.data?.stations ?? NO_STATIONS;
  usePlayback();

  const changeLayout = (next: PanelLayout): void => {
    if (next === (requestedLayout.current ?? layout)) return;
    requestedLayout.current = next;
    const update = (): void => {
      // Skipped transitions still invoke their callback; only apply the latest click.
      if (requestedLayout.current !== next) return;
      requestedLayout.current = null;
      flushSync(() => { useMapStore.getState().setPanelLayout(next); });
    };
    const target = grid.current;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (typeof target?.startViewTransition !== "function" || reducedMotion) {
      update();
      if (target !== null && !reducedMotion) {
        target.removeAttribute("data-motion");
        // Resolve the removed animation so the next layout can replay its entrance.
        target.getAnimations();
        target.dataset.motion = "fade";
      }
      return;
    }
    void target.startViewTransition(update).ready.catch(() => undefined);
  };

  const availabilities = useMemo(
    () => new Set<StationAvailability>(stations.map((station) => station.availability)),
    [stations],
  );

  return (
    <div className={styles.frame}>
      <div ref={grid} className={styles.grid} data-layout={layout}>
        {Array.from({ length: layout }, (_, panel) => (
          <MapPanel key={panel} panel={panel} layout={layout} onRuntime={panel === 0 ? setRuntime : undefined} />
        ))}
      </div>
      <VisuallyHidden id={MAP_HINT_ID}>
        Arrow keys pan the map and plus and minus zoom. Select stations with the station search.
        {" Inspect shows map hover readouts; Navigate hides them."}
        {live ? null : " Click to pick a forecast point, or pan with the arrow keys and press Enter to inspect the point at the centre of the map."}
      </VisuallyHidden>
      <StationTooltip />
      <OutlookTooltip />
      <ReportTooltip />
      {slots.left === null || runtime === null
        ? null
        : createPortal(
            <MapToolbar
              onLayoutChange={changeLayout}
              onZoomIn={() => {
                runtime.zoomBy(1);
              }}
              onZoomOut={() => {
                runtime.zoomBy(-1);
              }}
              onResetView={() => {
                runtime.resetView();
              }}
            />,
            slots.left,
          )}
      {slots.centre === null
        ? null
        : createPortal(
            <div className={styles.notices}>
              <CatalogueStatusNotice surface="floating" />
              <MapNotices />
            </div>,
            slots.centre,
          )}
      {slots.rightEnd === null ? null : createPortal(<LayerPanel availabilities={availabilities} />, slots.rightEnd)}
    </div>
  );
}

export default function WeatherMap(): ReactNode {
  const view = useMapStore((state) => state.view);
  const antialias = useGraphicsStore((state) => profileSettings(state.effective.profile).antialias);
  const attempt = useGraphicsStore((state) => state.mapAttempt);
  return <MapView key={`${view}:${String(antialias)}:${String(attempt)}`} />;
}
