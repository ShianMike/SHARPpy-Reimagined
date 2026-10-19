import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Layers, LoaderCircle, Map, MapPin, Maximize, Minimize, X } from "lucide-react";
import { VisuallyHidden } from "../components/VisuallyHidden";
import { useBackendHealth } from "../lib/backend/BackendProvider";
import { ReducedRenderingView } from "../features/fallback/ReducedRenderingView";
import { Inspector } from "../features/inspector/Inspector";
import { NavigationRail } from "../features/rail/NavigationRail";
import { SettingsView } from "../features/settings/SettingsView";
import { UpdateNotice } from "../features/settings/UpdateNotice";
import { WhatsNew } from "../features/settings/WhatsNew";
import { SoundingWorkspace } from "../features/sounding/SoundingWorkspace";
import { TimelineRegion } from "../features/timeline/TimelineRegion";
import { LiveTimeline } from "../features/tracking/LiveTimeline";
import { useTheme, useUiStore } from "../stores/uiStore";
import { EmptyPanel, IconAction } from "../ui/Controls";
import { Surface } from "../ui/Surface";
import { OperationsWorkspace } from "../features/operations/OperationsWorkspace";
import { LogsWorkspace } from "../features/operations/LogsWorkspace";
import { Toolbar } from "../features/toolbar/Toolbar";
import { useAppStore } from "../stores/appStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useForecastStore } from "../stores/forecastStore";
import { stationIndex } from "../stations/stationIndex";
import { useStationCatalog } from "../stations/stationQueries";
import { MapOverlaySlotsProvider, useSlotElements } from "./MapOverlaySlots";
import { useGlobalShortcuts } from "./shortcuts";
import { useFullscreen } from "./useFullscreen";
import { workspaceDefinition, type WorkspaceDefinition } from "./workspaces";
import styles from "./Shell.module.css";

const WeatherMap = lazy(() => import("../map/WeatherMap"));

function useCatalogueEffects(): string {
  const stations = useStationCatalog().data?.stations;
  useEffect(() => {
    if (stations !== undefined) {
      const index = stationIndex(stations);
      useAppStore.getState().reconcilePendingSelection((id) => index.has(id));
    }
  }, [stations]);
  return stations === undefined ? "" : `${String(stations.length)} stations loaded`;
}

function WorkspaceView({ workspace }: { workspace: WorkspaceDefinition }): ReactNode {
  switch (workspace.id) {
    case "map": return null;
    case "live": return null;
    case "overview": return <OperationsWorkspace />;
    case "status": return <LogsWorkspace />;
    case "sounding": return <SoundingWorkspace />;
    case "settings": return <SettingsView />;
  }
}

export function Shell(): ReactNode {
  const whatsNewOpen = useUiStore((state) => state.whatsNewOpen);
  useGlobalShortcuts();
  const { fullscreen, busy: fullscreenBusy, error: fullscreenError, toggle: toggleFullscreen } = useFullscreen();
  useTheme();
  useBackendHealth();
  const workspace = workspaceDefinition(useAppStore((state) => state.activeWorkspace));
  const mode = useGraphicsStore((state) => state.effective.mode);
  const renderer = useGraphicsStore((state) => state.renderer);
  const hasSelection = useAppStore((state) => state.selectedStationId !== null || (state.activeWorkspace !== "live" && state.selectedPoint !== null));
  const [sideTab, setSideTab] = useState<"point" | "layers">("point");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const toggleDetails = (tab: "point" | "layers"): void => {
    setSideTab(tab); setDetailsOpen(!(detailsOpen && sideTab === tab));
  };
  const detailTools = useRef<HTMLDivElement>(null);
  const closeDetails = (): void => {
    detailTools.current?.querySelector<HTMLButtonElement>(`[aria-label="${sideTab === "layers" || isLive ? "Layers" : "Point details"}"]`)?.focus();
    setDetailsOpen(false);
  };
  const { slots, attachLeft, attachCentre, attachRightEnd } = useSlotElements();
  const liveText = useCatalogueEffects();
  const isLive = workspace.id === "live";
  const isMap = workspace.id === "map" || isLive;
  const [panelWorkspace, setPanelWorkspace] = useState<WorkspaceDefinition | null>(isMap ? null : workspace);
  const workspacePanel = useRef<HTMLElement>(null);
  if (!isMap && panelWorkspace?.id !== workspace.id) setPanelWorkspace(workspace);
  useLayoutEffect(() => {
    if (!isMap || panelWorkspace === null) return;
    let closing = true;
    const element = workspacePanel.current;
    const animations = element !== null && typeof element.getAnimations === "function" ? element.getAnimations() : [];
    void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (closing) setPanelWorkspace(null);
    });
    return () => { closing = false; };
  }, [isMap, panelWorkspace]);
  useEffect(() => { if (workspace.id !== "map") useForecastStore.getState().setPlaying(false); }, [workspace.id]);
  useEffect(() => useAppStore.subscribe(
    (state) => state.selectedPoint ?? state.selectedStationId,
    (selection) => { setDetailsOpen(selection !== null); if (selection !== null) setSideTab("point"); },
  ), []);
  return (
    <MapOverlaySlotsProvider slots={slots}>
      <div className={styles.shell} data-live={isLive}>
        <Toolbar className={styles.toolbar} isMap={isMap} live={isLive}>
          <div ref={detailTools} className={styles.detailTools} role="group" aria-label="Map detail views" hidden={!isMap}>
            <IconAction icon={Layers} label="Layers" hintSide="bottom-end" aria-expanded={detailsOpen && sideTab === "layers"} aria-controls="map-details"
              onClick={() => { toggleDetails("layers"); }} />
            {isLive ? null : <IconAction icon={MapPin} label="Point details" hintSide="bottom-end" aria-expanded={detailsOpen && sideTab === "point"} aria-controls="map-details"
              onClick={() => { toggleDetails("point"); }} />}
            <IconAction icon={fullscreen ? Minimize : Maximize} label={fullscreenError || (fullscreen ? "Exit fullscreen" : "Enter fullscreen")} hintSide="bottom-end"
              aria-pressed={fullscreen} aria-keyshortcuts="F11" disabled={fullscreenBusy} onClick={() => { void toggleFullscreen(); }} />
          </div>
        </Toolbar>
        <div className={styles.leftDock}>
          <NavigationRail className={styles.rail} />
          <div ref={attachLeft} className={styles.mapTools} data-motion="fade" inert={!isMap} hidden={!isMap} />
        </div>
        <main className={styles.main}>
          <section className={styles.mapSurface} aria-label={isLive ? "Live tracking map" : "Model map"} inert={!isMap}>
            {mode === "full" && renderer !== "failed"
              ? <Suspense fallback={null}><WeatherMap /></Suspense>
              : <ReducedRenderingView />}
            {mode === "full" && renderer === "pending" ? <Surface className={styles.mapLoading} role="status" aria-label="Loading map" data-motion="enter" hidden={!isMap}>
              <span className={styles.loadingIcon} aria-hidden="true"><Map /></span>
              <h2>Loading map</h2>
              <p><LoaderCircle className="icon icon-sm" data-loading="true" aria-hidden="true" />Preparing your weather workspace…</p>
            </Surface> : null}
          </section>
          <div className={styles.viewer} data-motion="fade" inert={!isMap} hidden={!isMap}>
            <div className={styles.overlay} data-testid="map-overlay">
              <div ref={attachCentre} className={styles.centre} />
            </div>
            <Surface as="aside" id="map-details" className={styles.sidePanel} data-motion="panel" data-view={sideTab} aria-label="Map details" inert={!detailsOpen} hidden={!detailsOpen}
              onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeDetails(); } }}>
              <div className={styles.sideHeader} role="group" aria-label="Detail panel">
                <h2>{sideTab === "layers" ? <Layers className="icon" aria-hidden="true" /> : <MapPin className="icon" aria-hidden="true" />}{sideTab === "layers" ? "Layers" : "Point"}</h2>
                {isLive && sideTab === "layers" ? null : <IconAction icon={sideTab === "layers" ? MapPin : Layers} size="sm"
                  label={sideTab === "layers" ? "Show point details" : "Show layers"}
                  onClick={() => { setSideTab(sideTab === "layers" ? "point" : "layers"); }} />}
                <IconAction icon={X} size="sm" label="Close map details" onClick={closeDetails} />
              </div>
              <div className={styles.point} data-motion="fade" hidden={sideTab !== "point"}>
                {hasSelection ? <Inspector /> : <EmptyPanel icon={MapPin} title={isLive ? "Explore live weather" : "Explore a forecast point"}
                  message={isLive ? "Hover over weather layers for details, or choose a station to open its sounding." : "Choose a location on the map to see its weather values and open a sounding."} />}
              </div>
              <div ref={attachRightEnd} className={styles.rightEnd} data-motion="fade" hidden={sideTab !== "layers"} />
            </Surface>
            {isLive ? <LiveTimeline className={styles.timeline} /> : <TimelineRegion className={styles.timeline} />}
          </div>
          {panelWorkspace === null ? null : <Surface key={panelWorkspace.id} ref={workspacePanel} as="section" className={styles.workspace} data-motion="panel" data-workspace={panelWorkspace.id} data-testid="workspace-panel" aria-label={panelWorkspace.label} inert={isMap} aria-hidden={isMap} hidden={isMap}>
            <WorkspaceView workspace={panelWorkspace} />
          </Surface>}
        </main>
        <div className={styles.updateNotice}><UpdateNotice /></div>
        {whatsNewOpen ? <WhatsNew /> : null}
        <VisuallyHidden role="status" aria-live="polite" data-testid={liveText === "" ? undefined : "catalogue-announcement"}>{liveText}</VisuallyHidden>
        {fullscreenError === "" ? null : <VisuallyHidden role="alert">{fullscreenError}</VisuallyHidden>}
      </div>
    </MapOverlaySlotsProvider>
  );
}
