// The deck.gl scene of one map panel (DESIGN-frontend F4, F5): the station
// array, the scene data WeatherMap hands in (field, radar, outlooks, domain,
// places), the label subsets chosen on moveend, and the setProps call that
// hands the registry's layers to the overlay. Data keeps stable references,
// so a rebuild never re-uploads a buffer that did not change.

import type { Layer } from "@deck.gl/core";
import { IconLayer, TextLayer } from "@deck.gl/layers";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useAppStore } from "../../stores/appStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { mapProjection, panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { stationIndex } from "../../stations/stationIndex";
import type { Station } from "../../types/contracts";
import { OUTLOOK_KEYS, type Place, type RadarSite } from "../../types/map";
import type { CanvasTokens } from "../canvasTokens";
import { declutter, declutterPoints } from "../labels/declutter";
import { declutterCellFor, equivalentSpan, labelTierFor, longitudeSpan } from "../labels/labelPolicy";
import { selectPlaceLabels } from "../labels/placeLabels";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { buildDeckLayers, layerInView, NO_SCENE_DATA, type LabelSelection, type LayerBuildContext, type LayerId, type LayerState, type SceneData } from "../layers/registry";
import { selectWindBarbs, type WindBarb } from "../layers/WindBarbLayer";
import { radarSiteCode } from "../panelRadar";
import type { WindBarbGrid } from "../../types/map";
import type { SurfacePlot } from "../../types/surface";

const NO_STATIONS: readonly Station[] = [];
const NO_PLACES: readonly Place[] = [];

/** A weather layer is drawn, so the boundaries go over it. */
function weatherShown(layers: Readonly<Record<LayerId, LayerState>>, data: SceneData): boolean {
  return (
    (layers["model-field"].visible && data.field !== null) ||
    (layers.satellite.visible && data.satellite !== null) ||
    (layers.radar.visible && data.radar !== null) ||
    (data.alerts?.some((alert) => layers[alert.kind === "warning" ? "warnings" : "watches"].visible) ?? false) ||
    OUTLOOK_KEYS.some((key) => layers[`spc-${key}` as const].visible && data.outlooks[key] !== undefined)
  );
}

export class StationScene {
  private stations: readonly Station[] = NO_STATIONS;
  private labels: LabelSelection | null = null;
  private placeLabels: readonly Place[] = NO_PLACES;
  private radarSiteLabels: readonly RadarSite[] = [];
  private surfacePlots: readonly SurfacePlot[] = [];
  private windBarbs: readonly WindBarb[] = [];
  private windGrid: WindBarbGrid | null = null;
  private windView = "";
  private labelView = "";
  private labelInputs: readonly unknown[] = [];
  private data: SceneData = NO_SCENE_DATA;
  /** The view's longitude span from the last moveend; full-world before it. */
  private span = 360;
  private weatherBeforeId: string | undefined;
  private outlookLabelLayout: LayerBuildContext["outlookLabelLayout"];
  /** The ids of the deck layers last submitted visible; the fixture probe reads them. */
  shownLayerIds: readonly string[] = [];

  constructor(
    private readonly overlay: MapLibreOverlay,
    private readonly tokens: CanvasTokens,
    private readonly labelFont: string,
    /** The panel this scene draws: 0 to 3. */
    readonly panel: number,
    /** Called after each setProps: true when a visible, non-empty station layer went out. */
    private readonly onSubmitted: (stationsShown: boolean) => void,
  ) {}

  setWeatherBeforeId(id: string | undefined): void {
    this.weatherBeforeId = id;
    this.render();
  }

  /** Returns false when the array is the one already shown. */
  setStations(stations: readonly Station[]): boolean {
    if (stations === this.stations) {
      return false;
    }
    this.stations = stations;
    return true;
  }

  /** Merges scene data; returns false when nothing changed. */
  setData(patch: Partial<SceneData>): boolean {
    const keys = Object.keys(patch) as (keyof SceneData)[];
    if (keys.every((key) => patch[key] === this.data[key])) {
      return false;
    }
    this.data = { ...this.data, ...patch };
    return true;
  }

  station(id: string): Station | undefined {
    return stationIndex(this.stations).get(id);
  }

  labelCount(): number {
    return this.labels?.stations.length ?? 0;
  }

  /** The label tier in use, or null when no labels are shown. */
  labelTier(): LabelSelection["tier"] | null {
    return this.labels?.tier ?? null;
  }

  /** The station label tier and subset for the view, then the place names around them. */
  updateLabels(map: MapLibreMap): void {
    const profile = useGraphicsStore.getState().effective.profile;
    const bounds = map.getBounds();
    const canvas = map.getCanvas();
    const viewport = { width: canvas.clientWidth, height: canvas.clientHeight };
    // The tiers follow the zoom, not the panel: a narrow panel takes the span
    // the window-wide single map shows at this zoom, so labels do not crowd.
    const span = equivalentSpan(longitudeSpan(bounds.getWest(), bounds.getEast()), viewport.width, window.innerWidth);
    this.span = span;
    const tier = labelTierFor(profile, span);
    const project = (lon: number, lat: number) => map.project([lon, lat]);
    const wind = this.data.contours?.wind ?? null;
    const view = [bounds.getWest(), bounds.getEast(), bounds.getSouth(), bounds.getNorth(), viewport.width, viewport.height,
      map.getZoom(), map.getBearing(), map.getPitch(), profile].join();
    if (wind !== this.windGrid || view !== this.windView) {
      this.windGrid = wind;
      this.windView = view;
      this.windBarbs = wind === null ? [] : selectWindBarbs(wind, map, profile === "low" ? 88 : 64);
    }
    const state = useMapStore.getState();
    const layers = panelLayers(state, this.panel);
    const weather = panelWeather(state, this.panel);
    const radarId = weather.radarScope === "site" ? weather.radarSite ?? this.data.selectedRadarSite ?? null : null;
    const labelView = [view, window.innerWidth, useAppStore.getState().selectedStationId,
      layers.stations.visible, layers["station-labels"].visible, layers["place-labels"].visible, weather.radarScope, this.data.selectedRadarSite, radarId].join();
    const inputs = [this.stations, this.data.radarSites, this.data.surfacePlots, this.data.places];
    if (labelView === this.labelView && inputs.every((input, index) => input === this.labelInputs[index])) return;
    this.labelView = labelView;
    this.labelInputs = inputs;
    const radarLabels = [...this.data.radarSites];
    const pinned = radarLabels.findIndex((site) => site.id === radarId);
    if (pinned > 0) radarLabels.unshift(...radarLabels.splice(pinned, 1));
    const { labelSize, stationRadius } = this.tokens.lengths;
    const occupied: { x: number; y: number; halfWidth: number }[] = [];
    this.radarSiteLabels = radarLabels.filter((site) => {
      const { x, y } = project(site.longitude, site.latitude);
      if (!(x >= 0 && x <= viewport.width && y >= 0 && y <= viewport.height)) return false;
      const halfWidth = Math.max(2.5, radarSiteCode(site.id).length * 0.35) * labelSize + 2 * stationRadius;
      if (occupied.some((label) => Math.abs(y - label.y) < 2 * labelSize && Math.abs(x - label.x) < halfWidth + label.halfWidth)) return false;
      occupied.push({ x, y, halfWidth });
      return true;
    });
    const plots = [...(this.data.surfacePlots ?? [])].sort((a, b) =>
      Number(b.availability === "available") - Number(a.availability === "available") || Number(a.stale) - Number(b.stale) || a.id.localeCompare(b.id),
    );
    this.surfacePlots = declutterPoints(plots, (plot) => project(plot.longitude, plot.latitude), viewport,
      Math.max(84, ...plots.map((plot) => plot.id.length * this.tokens.lengths.labelSize + 28)));
    this.outlookLabelLayout = { ...viewport, project: ([lon, lat]) => project(lon, lat), occupied: this.surfacePlots.map((plot) => {
      const point = project(plot.longitude, plot.latitude);
      return { x: point.x + 12, y: point.y - 4, width: Math.max(84, plot.id.length * 8 + 40), height: 64 };
    }) };
    if (tier === null || this.stations.length === 0) {
      this.labels = null;
    } else {
      const stations = declutter(this.stations, {
        project: (station) => project(station.longitude, station.latitude),
        viewport,
        // shortcut: reserve the longest ID for every label; use per-label bounds if density becomes limiting.
        cell: Math.max(declutterCellFor(profile), ...this.stations.map((station) => (station.id.length + 1) * this.tokens.lengths.labelSize)),
        selectedId: useAppStore.getState().selectedStationId,
      });
      this.labels = { stations, tier };
    }
    if (import.meta.env.MODE !== "fixture" || !layers["place-labels"].visible || this.data.places.length === 0) {
      this.placeLabels = NO_PLACES;
      return;
    }
    const showsStationLabels = layers["station-labels"].visible && layers.stations.visible;
    this.placeLabels = selectPlaceLabels({
      places: this.data.places,
      bounds: { west: bounds.getWest(), east: bounds.getEast(), south: bounds.getSouth(), north: bounds.getNorth() },
      span,
      project: (place) => project(place.longitude, place.latitude),
      viewport,
      cell: 2 * declutterCellFor(profile),
      reserved: showsStationLabels ? (this.labels?.stations ?? []).map((s) => project(s.longitude, s.latitude)) : [],
      reservedCell: declutterCellFor(profile),
    });
  }

  render(): void {
    const mapState = useMapStore.getState();
    const layers = panelLayers(mapState, this.panel);
    const weather = panelWeather(mapState, this.panel);
    const app = useAppStore.getState();
    const selectedId = app.selectedStationId;
    const built: Layer[] = buildDeckLayers(
      LAYER_REGISTRY.filter(({ id }) => layerInView(id, mapState.view)),
      layers,
      {
        ...this.data,
        surfacePlots: this.surfacePlots,
        windBarbs: this.windBarbs,
        point: mapState.view === "live" ? null : app.selectedPoint,
        stations: this.stations,
        labels: this.labels,
        selected: selectedId === null ? null : (this.station(selectedId) ?? null),
        tokens: this.tokens,
        labelFont: this.labelFont,
        placeLabels: this.placeLabels,
        radarSites: this.radarSiteLabels,
        selectedRadarSite: mapState.view === "live" && weather.radarScope === "site" ? weather.radarSite ?? this.data.selectedRadarSite ?? null : null,
        span: this.span,
        basemap: layers.basemap,
        weatherShown: weatherShown(layers, this.data),
        weatherBeforeId: this.weatherBeforeId,
        outlookLabelLayout: this.outlookLabelLayout === undefined ? undefined : {
          ...this.outlookLabelLayout,
          occupied: layers["surface-plots"].visible ? this.outlookLabelLayout.occupied : [],
        },
      },
      mapState.layerOrder,
    );
    // Globe geometry can slice through screen-facing labels and icons at ground level.
    this.overlay.setProps({ layers: mapProjection(mapState) === "globe"
      ? built.map((layer): Layer => layer instanceof TextLayer || layer instanceof IconLayer
        ? layer.clone({ parameters: { cullMode: "none", depthCompare: "always", depthWriteEnabled: false } }) as Layer : layer) : built });
    this.shownLayerIds = built.filter((layer) => layer.props.visible).map((layer) => layer.id);
    this.onSubmitted(this.stations.length > 0 && layers.stations.visible);
  }
}

export { clickFromPick, hoverFromPick } from "./stationPicking";
