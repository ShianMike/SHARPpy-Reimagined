// One panel's MapLibre map and deck.gl overlay. Panel 0 reports startup,
// notices, and the camera snapshot; cameraLink synchronizes the other panels.

import "./maplibreWorker";
import * as maplibregl from "maplibre-gl";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import type { Station } from "../../types/contracts";
import { documentTokenReader, readCameraDurationMs, readCanvasTokens, type CanvasTokens } from "../canvasTokens";
import { applyBasemapLabels, prepareBasemap } from "../basemap/basemapLayers";
import { basemapStyle } from "../basemap/basemapStyle";
import { useHoverStore } from "../hover/hoverStore";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { applyStyleLayers, type SceneData } from "../layers/registry";
import { useMapNoticeStore } from "../notices/mapNoticeStore";
import { CAMERA_SNAPSHOT_DEBOUNCE_MS, CONUS_BOUNDS, FOCUS_ZOOM, isValidCamera } from "./camera";
import { linkCamera } from "./cameraLink";
import { mapOptions, panelPixelRatio } from "./mapOptions";
import { createPanelOverlay, wireMapEvents, wireOverlayEvents, type RuntimeEventTarget } from "./runtimeEvents";
import { createRuntimeWatch, NO_WATCH, subscribeToStores } from "./runtimeWiring";
import type { StartupWatch } from "./startupWatch";
import { StationScene } from "./stationScene";

type E2eModule = typeof import("./e2eHooks");
type E2eTarget = import("./e2eHooks").E2eTarget; // type-only: the module loads in fixture builds alone

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const NO_PADDING = { top: 0, bottom: 0, left: 0, right: 0 };

export class MapRuntime implements RuntimeEventTarget {
  private disposed = false;
  /** Read by the fixture-only probe, as are scene, container, and errors. */
  map: maplibregl.Map | null = null;
  private overlay: MapLibreOverlay | null = null;
  scene: StationScene | null = null;
  readonly watch: StartupWatch;
  readonly primary: boolean;
  private tokens: CanvasTokens | null = null;
  private styleReady = false;
  overlayReady = false;
  dragging = false;
  private snapshotTimer: ReturnType<typeof setTimeout> | null = null;
  private e2e: E2eModule | null = null;
  private probe: E2eTarget | null = null;
  readonly errors: string[] = [];
  private readonly unsubscribers: (() => void)[] = [];
  constructor(
    readonly container: HTMLElement,
    readonly panel = 0,
  ) {
    this.primary = panel === 0;
    if (this.primary) {
      // Every new primary runtime starts pending, with no lost context and no notices.
      useGraphicsStore.getState().beginRendererAttempt();
      useMapNoticeStore.getState().reset();
    }
    this.watch = this.primary ? createRuntimeWatch() : NO_WATCH;
    const profile = useGraphicsStore.getState().effective.profile;
    try {
      const read = documentTokenReader();
      const tokens = readCanvasTokens(read);
      this.tokens = tokens;
      const map = new maplibregl.Map({ ...mapOptions(container, tokens, profile), pixelRatio: this.pixelRatio() });
      this.map = map;
      wireMapEvents(map, this);
      // Right after construction: the stations never wait for the basemap.
      const overlay = createPanelOverlay(tokens, this.pixelRatio(), this);
      this.overlay = overlay;
      map.addControl(overlay);
      if (import.meta.env.MODE !== "fixture" && useMapStore.getState().projection === "mercator") this.unsubscribers.push(wireOverlayEvents(map, overlay, this, tokens.lengths.pickingRadius));
      this.scene = new StationScene(overlay, tokens, read("--font-mono").trim(), panel, (shown) => { this.watch.layersSubmitted(shown); });
    } catch (error) {
      this.failConstruction(error);
      return;
    }
    this.initialView(this.map);
    this.unsubscribers.push(linkCamera(this.map, this.live(() => { this.moveEnded(); })));
    this.subscribe();
    this.redraw();
    if (import.meta.env.MODE === "fixture" && this.primary) {
      void Promise.all([import("./e2eHooks"), import("./runtimeProbe")]).then(([hooks, probe]) => {
        if (!this.disposed) {
          this.e2e = hooks;
          this.probe = probe.createRuntimeProbe(() => this);
          hooks.install(this.probe);
        }
      });
    }
  }

  /** The catalogue's station array; kept by reference so deck.gl keeps its buffers. */
  setStations(stations: readonly Station[]): void {
    if (!this.disposed && this.scene?.setStations(stations) === true) {
      this.redraw();
    }
  }

  /** The field, overlays, domain, and places this panel draws. */
  setScene(patch: Partial<SceneData>): void {
    if (!this.disposed && this.scene?.setData(patch) === true) {
      if (patch.places !== undefined || patch.surfacePlots !== undefined || patch.contours !== undefined) {
        this.redraw();
      } else {
        this.scene.render();
      }
    }
  }

  /** Zoom in (+1) or out (-1) by one level. */
  zoomBy(delta: 1 | -1): void {
    this.map?.zoomTo(this.map.getZoom() + delta, { duration: this.cameraDuration() });
  }

  resetView(): void {
    this.map?.fitBounds(CONUS_BOUNDS, { duration: this.cameraDuration() });
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }
    // First: every callback this runtime registered becomes a no-op, so a context
    // loss that map.remove() causes, delivered after the next map exists, is ignored.
    this.disposed = true;
    this.watch.dispose();
    for (const unsubscribe of this.unsubscribers.splice(0)) {
      unsubscribe();
    }
    if (this.snapshotTimer !== null) {
      clearTimeout(this.snapshotTimer);
    }
    if (this.probe !== null) {
      this.e2e?.uninstall(this.probe);
    }
    useHoverStore.getState().clear();
    try {
      this.map?.remove();
    } catch (error) {
      console.warn("[map] the map could not be removed cleanly", error);
    }
  }

  live<T extends unknown[]>(handler: (...args: T) => void): (...args: T) => void {
    return (...args) => {
      if (!this.disposed) {
        handler(...args);
      }
    };
  }

  styleLoaded(): void {
    this.styleReady = true;
    this.map?.setProjection({ type: useMapStore.getState().projection });
    if (this.map !== null && import.meta.env.MODE !== "fixture") this.scene?.setWeatherBeforeId(prepareBasemap(this.map));
    this.applyStyleState();
  }

  moveEnded(): void {
    this.redraw();
    this.scheduleSnapshot();
  }

  runtimeError(message: string): void {
    console.warn(`[map] ${message}`);
    useMapNoticeStore.getState().addRuntimeError();
  }

  private pixelRatio(): number {
    return panelPixelRatio(useGraphicsStore.getState().effective.profile, useMapStore.getState().panelLayout);
  }

  private failConstruction(error: unknown): void {
    const message = messageOf(error);
    console.warn(`[map] panel ${String(this.panel + 1)} could not be constructed:`, message);
    this.watch.dispose();
    try {
      this.map?.remove();
    } catch {
      // The constructor failed part-way; there is nothing more to release.
    }
    this.map = null;
    this.overlay = null;
    if (this.primary) {
      useGraphicsStore.getState().setRendererFailed("constructor", message);
    }
  }

  private initialView(map: maplibregl.Map): void {
    this.applyPadding();
    const saved = useMapStore.getState().cameraSnapshot;
    if (isValidCamera(saved)) {
      map.jumpTo({ center: saved.center, zoom: saved.zoom });
    } else {
      map.fitBounds(CONUS_BOUNDS, { animate: false });
    }
  }

  /** The map is contained within the workspace, clear of application chrome. */
  private applyPadding(): void {
    if (this.map !== null && this.tokens !== null) {
      this.map.setPadding(NO_PADDING);
    }
  }

  private subscribe(): void {
    let theme = document.documentElement.dataset.theme;
    const themeObserver = new MutationObserver(() => {
      const next = document.documentElement.dataset.theme;
      if (next === theme || this.map === null || this.tokens === null) return;
      theme = next;
      this.styleReady = false;
      this.scene?.setWeatherBeforeId(undefined);
      this.map.setStyle(basemapStyle(this.tokens), { diff: false });
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.unsubscribers.push(() => { themeObserver.disconnect(); });
    const applyPixelRatio = (): void => {
      const ratio = this.pixelRatio();
      this.map?.setPixelRatio(ratio);
      this.overlay?.setProps({ useDevicePixels: ratio });
    };
    this.unsubscribers.push(
      ...subscribeToStores(this.panel, {
        selectionChanged: () => {
          this.redraw();
        },
        layersChanged: (labelsChanged) => {
          this.applyStyleState();
          if (labelsChanged) this.redraw();
          else this.scene?.render();
        },
        focus: (stationId) => {
          this.focus(stationId);
        },
        profileChanged: () => {
          applyPixelRatio();
          this.redraw();
        },
        pixelRatioChanged: applyPixelRatio,
      }),
      useMapStore.subscribe(
        (state) => state.panelLayout,
        () => {
          applyPixelRatio();
          this.applyPadding();
          this.map?.resize();
        },
      ),
    );
  }

  private redraw(): void {
    if (!this.disposed && this.map !== null && this.scene !== null) {
      this.scene.updateLabels(this.map);
      this.scene.render();
    }
  }

  private applyStyleState(): void {
    if (!this.disposed && this.styleReady && this.map !== null) {
      const layers = panelLayers(useMapStore.getState(), this.panel);
      applyStyleLayers(this.map, LAYER_REGISTRY, layers);
      applyBasemapLabels(this.map, layers["place-labels"]);
    }
  }

  private focus(stationId: string): void {
    const station = this.scene?.station(stationId);
    if (this.disposed || !this.primary || this.map === null || station === undefined) {
      return;
    }
    const zoom = Math.max(this.map.getZoom(), FOCUS_ZOOM);
    this.map.flyTo({ center: [station.longitude, station.latitude], zoom, duration: this.cameraDuration() });
  }

  private cameraDuration(): number {
    return readCameraDurationMs(documentTokenReader());
  }

  private scheduleSnapshot(): void {
    if (!this.primary) {
      return;
    }
    if (this.snapshotTimer !== null) {
      clearTimeout(this.snapshotTimer);
    }
    this.snapshotTimer = setTimeout(() => {
      this.snapshotTimer = null;
      const map = this.map;
      if (!this.disposed && map !== null) {
        const center = map.getCenter();
        useMapStore.getState().setCameraSnapshot({ center: [center.lng, center.lat], zoom: map.getZoom() });
      }
    }, CAMERA_SNAPSHOT_DEBOUNCE_MS);
  }
}
