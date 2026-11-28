// The typed layer registry (DESIGN-frontend F4). A layer is a descriptor plus
// its module; WeatherMap.tsx never changes when one is added. This module holds
// the types and the pure functions, and imports MapLibre and deck.gl types only,
// so the stores can import it without pulling either library into the shell
// chunk. The descriptors themselves are in descriptors.ts.

import type { Layer } from "@deck.gl/core";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { CanvasTokens } from "../canvasTokens";
import type { LabelTier } from "../../stations/stationFormat";
import type { Station } from "../../types/contracts";
import type { ContourLines, FieldFrame, Outlook, OutlookLayerKey, Palette, Place, ProductContour, RadarSite, ReportPoint } from "../../types/map";
import type { Bounds } from "../../types/soundings";
import type { SpotterPosition } from "../../types/spotters";
import type { SurfacePlot } from "../../types/surface";
import type { WeatherAlert } from "../../types/weatherAlerts";
import type { WindBarb } from "./WindBarbLayer";
import type { ScreenPoint, Viewport } from "../labels/declutter";

export const LAYER_IDS = [
  "basemap", "roads", "rivers", "stations", "station-labels", "selection", "model-field", "satellite", "radar",
  "storm-reports", "surface-plots", "spc-cat", "spc-torn", "spc-wind", "spc-hail", "wpc-ero", "wpc-winter",
  "boundaries", "model-domain", "place-labels", "watches", "warnings", "radar-site-labels", "isobars", "spotter-network",
] as const;
export type LayerId = (typeof LAYER_IDS)[number];

/** The weather layers, which the user may stack in any order; bottom first by default. */
export const ORDERABLE_LAYER_IDS = [
  "model-field", "satellite", "radar", "storm-reports", "spc-cat", "spc-torn", "spc-wind", "spc-hail", "wpc-ero", "wpc-winter",
  "isobars", "surface-plots", "watches", "warnings", "spotter-network",
] as const;
export type OrderableLayerId = (typeof ORDERABLE_LAYER_IDS)[number];

/** Keep controls and rendering consistent, including previously saved layers. */
export function layerInView(id: LayerId, view: "map" | "live"): boolean {
  if (id === "model-field" || id === "isobars" || id.startsWith("spc-") || id.startsWith("wpc-")) return view === "map";
  if (["radar", "satellite", "warnings", "watches", "radar-site-labels", "spotter-network"].includes(id)) return view === "live";
  return true;
}
/** The zOrder of the lowest weather layer; the rest follow at one step each. */
const ORDERABLE_BASE = 10;

export interface LayerState {
  visible: boolean;
  /** 0 to 1. */
  opacity: number;
  /** Optional display smoothing; omitted keeps source pixels sharp. */
  smoothing?: boolean;
}

/** Initial state of each layer; the descriptors take their defaults from here. */
export const LAYER_DEFAULTS: Readonly<Record<LayerId, LayerState>> = {
  basemap: { visible: true, opacity: 1 },
  roads: { visible: true, opacity: 1 },
  rivers: { visible: true, opacity: 1 },
  stations: { visible: true, opacity: 1 },
  "station-labels": { visible: false, opacity: 1 },
  "radar-site-labels": { visible: true, opacity: 1 },
  selection: { visible: true, opacity: 1 },
  "model-field": { visible: false, opacity: 0.5, smoothing: true },
  isobars: { visible: false, opacity: 1 },
  satellite: { visible: false, opacity: 0.72 },
  radar: { visible: false, opacity: 0.8 },
  watches: { visible: false, opacity: 1 },
  warnings: { visible: false, opacity: 1 },
  "storm-reports": { visible: false, opacity: 1 },
  "surface-plots": { visible: false, opacity: 1 },
  "spotter-network": { visible: false, opacity: 1 },
  "spc-cat": { visible: false, opacity: 1 },
  "spc-torn": { visible: false, opacity: 1 },
  "spc-wind": { visible: false, opacity: 1 },
  "spc-hail": { visible: false, opacity: 1 },
  "wpc-ero": { visible: false, opacity: 1 },
  "wpc-winter": { visible: false, opacity: 1 },
  boundaries: { visible: true, opacity: 1 },
  "model-domain": { visible: true, opacity: 1 },
  "place-labels": { visible: true, opacity: 1 },
};

/** The label subset chosen on the last moveend, or null when no label tier applies. */
export interface LabelSelection {
  stations: readonly Station[];
  tier: LabelTier;
}

/** A coloured field or radar frame, ready for a BitmapLayer. */
export interface RasterImage {
  image: ImageData | ImageBitmap;
  bounds: Bounds;
}

export interface FieldImage extends RasterImage {
  frame: FieldFrame;
  palette: Palette;
}

/** What the map shows besides the stations; set by WeatherMap from the queries. */
export interface SceneData {
  /** Enabled overlays have resolved for this time and been submitted with the scene. */
  captureReady: boolean;
  captureTime: string | null;
  /** This panel's coloured model field. */
  field: FieldImage | null;
  previousField?: FieldImage | null;
  fieldMix?: number;
  /** The GOES frame, rendered by the backend over the map's extent. */
  satellite: RasterImage | null;
  radar: RasterImage | null;
  reports: readonly ReportPoint[] | null;
  alerts: readonly WeatherAlert[] | null;
  spotterPositions: readonly SpotterPosition[] | null;
  /** The station models to mark, each carrying its own display slots. */
  surfacePlots: readonly SurfacePlot[] | null;
  /** The product's contour lines and the spec that draws them; both or neither. */
  contours: ContourLines | null;
  contourSpec: ProductContour | null;
  /** Mean-sea-level pressure, independent of the selected field and its contours. */
  isobars: ContourLines | null;
  outlooks: Readonly<Partial<Record<OutlookLayerKey, Outlook>>>;
  /** The selected model's domain, a closed [lon, lat] path. */
  domain: readonly (readonly [number, number])[] | null;
  /** All place names; the label pass picks the subset to draw. */
  places: readonly Place[];
  radarSites: readonly RadarSite[];
  selectedRadarSite: string | null;
  unavailableRadarSites: ReadonlySet<string>;
  /** The selected forecast point. */
  point: { latitude: number; longitude: number } | null;
}

export const NO_SCENE_DATA: SceneData = {
  captureReady: false,
  captureTime: null,
  field: null,
  previousField: null,
  fieldMix: 1,
  satellite: null,
  radar: null,
  reports: null,
  alerts: null,
  surfacePlots: null,
  spotterPositions: null,
  contours: null,
  contourSpec: null,
  isobars: null,
  outlooks: {},
  domain: null,
  places: [],
  radarSites: [],
  selectedRadarSite: null,
  unavailableRadarSites: new Set<string>(),
  point: null,
};

export interface LayerBuildContext extends SceneData {
  windBarbs?: readonly WindBarb[];
  /** The camera-dependent detail subset and whether to draw every surface station center. */
  surfacePresentation?: { details: readonly SurfacePlot[]; markers: boolean };
  /** The catalogue's array, kept as a stable reference so deck.gl keeps its buffers. */
  stations: readonly Station[];
  labels: LabelSelection | null;
  selected: Station | null;
  tokens: CanvasTokens;
  /** The system monospace stack (--font-mono). */
  labelFont: string;
  /** The place-name subset chosen on the last moveend. */
  placeLabels: readonly Place[];
  /** The view's longitude span, window-wide equivalent (labelPolicy). */
  span: number;
  /** The basemap's state: the boundaries drawn over the weather follow it. */
  basemap: LayerState;
  /** A weather layer is drawn, so the boundaries go over it. */
  weatherShown: boolean;
  /** Native outlines above the weather stack in interleaved maps. */
  weatherBeforeId?: string | undefined;
  /** Pressure components supplied by their preferred visible layer in this scene. */
  pressureOwnership?: { independentIsobars: boolean; compositeThickness: boolean };
  /** Shared screen bounds for weather labels, rebuilt once per scene update. */
  outlookLabelLayout?: (Viewport & {
    project: (position: readonly [number, number]) => ScreenPoint;
    occupied: (ScreenPoint & Viewport)[];
    /** Current projection state when report captions can be reused. */
    reportCaptionView?: () => string | undefined;
  }) | undefined;
}

export interface LayerDescriptor<Id extends string = LayerId> {
  id: Id;
  /** Shown in the layer panel. */
  label: string;
  kind: "maplibre-style" | "deck";
  zOrder: number;
  userControllable: boolean;
  /** The layer panel shows an opacity slider for it. */
  opacityControl: boolean;
  defaults: LayerState;
  /** deck.gl layers; they must take visible and opacity from the state. */
  build?: (ctx: LayerBuildContext, state: LayerState) => readonly Layer[];
  /** MapLibre style layers. */
  apply?: (map: MapLibreMap, state: LayerState) => void;
}

export function clampOpacity(value: number): number {
  return Number.isNaN(value) ? 0 : Math.min(1, Math.max(0, value));
}
function stateFor<Id extends string>(descriptor: LayerDescriptor<Id>, states: Partial<Readonly<Record<Id, LayerState>>>): LayerState {
  const state = states[descriptor.id] ?? descriptor.defaults;
  return { ...state, opacity: clampOpacity(state.opacity) };
}
function zOrderOf<Id extends string>(descriptor: LayerDescriptor<Id>, order: readonly string[]): number {
  const index = order.indexOf(descriptor.id);
  return index === -1 ? descriptor.zOrder : ORDERABLE_BASE + index;
}
function byZOrder<Id extends string>(descriptors: readonly LayerDescriptor<Id>[], order: readonly string[] = ORDERABLE_LAYER_IDS): LayerDescriptor<Id>[] {
  return [...descriptors].sort((a, b) => zOrderOf(a, order) - zOrderOf(b, order));
}

/** Build deck.gl layers in z-order. Layer props preserve GPU buffers when toggled. */
export function buildDeckLayers<Id extends string>(
  descriptors: readonly LayerDescriptor<Id>[],
  states: Partial<Readonly<Record<Id, LayerState>>>,
  ctx: LayerBuildContext,
  order: readonly string[] = ORDERABLE_LAYER_IDS,
): Layer[] {
  const out: Layer[] = [];
  const labels: Layer[] = [];
  const thickness = ctx.contourSpec !== null && ctx.contours?.unit === "hPa" ? ctx.contours.thickness : undefined;
  const isobarOverlay = ctx.isobars === null ? undefined : descriptors.find(({ id, kind, build }) =>
    id === "isobars" && kind === "deck" && build !== undefined);
  const composite = thickness === undefined ? undefined : descriptors.find(({ id, kind, build }) =>
    id === "model-field" && kind === "deck" && build !== undefined);
  const isobarState = isobarOverlay && stateFor(isobarOverlay, states);
  const modelState = composite && stateFor(composite, states);
  const independentIsobars = isobarState?.visible === true && isobarState.opacity > 0;
  // A shared frame uses the independent layer's opacity for its whole pressure overlay.
  const compositeThickness = modelState?.visible === true && modelState.opacity > 0 && thickness !== undefined
    && (thickness.paths.length > 0 || thickness.labels.length > 0) && (!independentIsobars || ctx.contours !== ctx.isobars);
  const layout = ctx.outlookLabelLayout;
  const context: LayerBuildContext = {
    ...ctx,
    pressureOwnership: { independentIsobars, compositeThickness },
    outlookLabelLayout: layout === undefined ? undefined : { ...layout, occupied: [...layout.occupied] },
  };
  let isobarLayers: readonly Layer[] | undefined;
  for (const descriptor of byZOrder(descriptors, order)) {
    if (descriptor.kind === "deck" && descriptor.build !== undefined) {
      // Reserve the hPa readings before thickness labels while keeping the user's drawing order.
      if (descriptor === composite && layout !== undefined && compositeThickness && independentIsobars) {
        isobarLayers ??= isobarOverlay?.build?.(context, isobarState);
      }
      const state = stateFor(descriptor, states);
      const layers = descriptor === isobarOverlay ? (isobarLayers ??= descriptor.build(context, state)) : descriptor.build(context, state);
      for (const layer of layers) {
        // Keep readings above every weather fill, even when the user reorders overlays.
        const contourOrOutlookLabels = layer.id.endsWith("-labels")
          && (descriptor.id === "model-field" || descriptor.id === "isobars" || layer.id.startsWith("spc-") || layer.id.startsWith("wpc-"));
        if (descriptor.id === "spotter-network" || descriptor.id === "surface-plots" || descriptor.id === "storm-reports" || contourOrOutlookLabels) {
          labels.push(layer);
          continue;
        }
        out.push(ctx.weatherBeforeId !== undefined && ORDERABLE_LAYER_IDS.includes(descriptor.id as OrderableLayerId)
          ? (layer as Layer<{ beforeId: string }>).clone({ beforeId: ctx.weatherBeforeId }) as Layer
          : layer);
      }
    }
  }
  return [...out, ...labels];
}

export function applyStyleLayers<Id extends string>(map: MapLibreMap, descriptors: readonly LayerDescriptor<Id>[], states: Partial<Readonly<Record<Id, LayerState>>>): void {
  for (const descriptor of byZOrder(descriptors)) {
    if (descriptor.kind === "maplibre-style" && descriptor.apply !== undefined) descriptor.apply(map, stateFor(descriptor, states));
  }
}
export function controllableLayers<Id extends string>(descriptors: readonly LayerDescriptor<Id>[], order: readonly string[] = ORDERABLE_LAYER_IDS): LayerDescriptor<Id>[] {
  return byZOrder(descriptors, order).filter((descriptor) => descriptor.userControllable);
}
export function defaultLayerStates(): Record<LayerId, LayerState> {
  return Object.fromEntries(LAYER_IDS.map((id) => [id, { ...LAYER_DEFAULTS[id] }])) as Record<LayerId, LayerState>;
}
export function moveInOrder<T extends string>(order: readonly T[], id: T, direction: 1 | -1): T[] {
  const index = order.indexOf(id), target = index + direction;
  if (index === -1 || target < 0 || target >= order.length) return [...order];
  const next = [...order];
  next[index] = order[target] as T;
  next[target] = id;
  return next;
}
