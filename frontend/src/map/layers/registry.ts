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
import type { ContourLines, FieldFrame, Outlook, OutlookKey, Palette, Place, ProductContour, RadarSite, ReportPoint } from "../../types/map";
import type { Bounds } from "../../types/soundings";
import type { SurfacePlot } from "../../types/surface";
import type { WeatherAlert } from "../../types/weatherAlerts";
import type { WindBarb } from "./WindBarbLayer";
import type { ScreenPoint, Viewport } from "../labels/declutter";

export const LAYER_IDS = [
  "basemap", "roads", "stations", "station-labels", "selection", "model-field", "satellite", "radar",
  "storm-reports", "surface-plots", "spc-cat", "spc-torn", "spc-wind", "spc-hail", "boundaries",
  "model-domain", "place-labels", "watches", "warnings", "radar-site-labels",
] as const;
export type LayerId = (typeof LAYER_IDS)[number];

/** The weather layers, which the user may stack in any order; bottom first by default. */
export const ORDERABLE_LAYER_IDS = [
  "model-field", "satellite", "radar", "storm-reports", "spc-cat", "spc-torn", "spc-wind", "spc-hail",
  "surface-plots", "watches", "warnings",
] as const;
export type OrderableLayerId = (typeof ORDERABLE_LAYER_IDS)[number];

/** Keep controls and rendering consistent, including previously saved layers. */
export function layerInView(id: LayerId, view: "map" | "live"): boolean {
  if (id === "model-field" || id.startsWith("spc-")) return view === "map";
  if (["radar", "satellite", "surface-plots", "warnings", "watches", "radar-site-labels"].includes(id)) return view === "live";
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
  stations: { visible: true, opacity: 1 },
  "station-labels": { visible: false, opacity: 1 },
  "radar-site-labels": { visible: true, opacity: 1 },
  selection: { visible: true, opacity: 1 },
  "model-field": { visible: false, opacity: 0.5, smoothing: true },
  satellite: { visible: false, opacity: 0.72 },
  radar: { visible: false, opacity: 0.8 },
  watches: { visible: false, opacity: 1 },
  warnings: { visible: false, opacity: 1 },
  "storm-reports": { visible: false, opacity: 1 },
  "surface-plots": { visible: false, opacity: 1 },
  "spc-cat": { visible: false, opacity: 1 },
  "spc-torn": { visible: false, opacity: 1 },
  "spc-wind": { visible: false, opacity: 1 },
  "spc-hail": { visible: false, opacity: 1 },
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
  /** This panel's coloured model field. */
  field: FieldImage | null;
  previousField?: FieldImage | null;
  fieldMix?: number;
  /** The GOES frame, rendered by the backend over the map's extent. */
  satellite: RasterImage | null;
  radar: RasterImage | null;
  /** The storm reports to mark, after the provider's own hazard filter. */
  reports: readonly ReportPoint[] | null;
  alerts: readonly WeatherAlert[] | null;
  /** The station models to mark, each carrying its own display slots. */
  surfacePlots: readonly SurfacePlot[] | null;
  /** The product's contour lines and the spec that draws them; both or neither. */
  contours: ContourLines | null;
  contourSpec: ProductContour | null;
  outlooks: Readonly<Partial<Record<OutlookKey, Outlook>>>;
  /** The selected model's domain, a closed [lon, lat] path. */
  domain: readonly (readonly [number, number])[] | null;
  /** All place names; the label pass picks the subset to draw. */
  places: readonly Place[];
  radarSites: readonly RadarSite[];
  /** The selected forecast point. */
  point: { latitude: number; longitude: number } | null;
}

export const NO_SCENE_DATA: SceneData = {
  field: null,
  previousField: null,
  fieldMix: 1,
  satellite: null,
  radar: null,
  reports: null,
  alerts: null,
  surfacePlots: null,
  contours: null,
  contourSpec: null,
  outlooks: {},
  domain: null,
  places: [],
  radarSites: [],
  point: null,
};

export interface LayerBuildContext extends SceneData {
  windBarbs?: readonly WindBarb[];
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
  /** Shared screen bounds for the visible outlook badges, rebuilt once per scene update. */
  outlookLabelLayout?: (Viewport & {
    project: (position: readonly [number, number]) => ScreenPoint;
    occupied: (ScreenPoint & Viewport)[];
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
  if (Number.isNaN(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value));
}

function stateFor<Id extends string>(
  descriptor: LayerDescriptor<Id>,
  states: Partial<Readonly<Record<Id, LayerState>>>,
): LayerState {
  const state = states[descriptor.id] ?? descriptor.defaults;
  return { ...state, opacity: clampOpacity(state.opacity) };
}

/** The zOrder of a descriptor, with the weather layers placed by the user's order. */
function zOrderOf<Id extends string>(descriptor: LayerDescriptor<Id>, order: readonly string[]): number {
  const index = order.indexOf(descriptor.id);
  return index === -1 ? descriptor.zOrder : ORDERABLE_BASE + index;
}

function byZOrder<Id extends string>(
  descriptors: readonly LayerDescriptor<Id>[],
  order: readonly string[] = ORDERABLE_LAYER_IDS,
): LayerDescriptor<Id>[] {
  return [...descriptors].sort((a, b) => zOrderOf(a, order) - zOrderOf(b, order));
}

/**
 * The deck.gl layers of every deck descriptor, lowest zOrder first. Visibility
 * and opacity reach deck.gl as layer props, so a toggle keeps the GPU buffers.
 */
export function buildDeckLayers<Id extends string>(
  descriptors: readonly LayerDescriptor<Id>[],
  states: Partial<Readonly<Record<Id, LayerState>>>,
  ctx: LayerBuildContext,
  order: readonly string[] = ORDERABLE_LAYER_IDS,
): Layer[] {
  const out: Layer[] = [];
  const labels: Layer[] = [];
  const context = ctx.outlookLabelLayout === undefined ? ctx : {
    ...ctx,
    outlookLabelLayout: { ...ctx.outlookLabelLayout, occupied: [...ctx.outlookLabelLayout.occupied] },
  };
  for (const descriptor of byZOrder(descriptors, order)) {
    if (descriptor.kind === "deck" && descriptor.build !== undefined) {
      const layers = descriptor.build(context, stateFor(descriptor, states));
      for (const layer of layers) {
        // Keep readings above every weather fill, even when the user reorders overlays.
        if (descriptor.id === "surface-plots" || descriptor.id === "storm-reports" || (layer.id.startsWith("spc-") && layer.id.endsWith("-labels"))) {
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

/** Applies the state of every MapLibre style descriptor. */
export function applyStyleLayers<Id extends string>(
  map: MapLibreMap,
  descriptors: readonly LayerDescriptor<Id>[],
  states: Partial<Readonly<Record<Id, LayerState>>>,
): void {
  for (const descriptor of byZOrder(descriptors)) {
    if (descriptor.kind === "maplibre-style" && descriptor.apply !== undefined) {
      descriptor.apply(map, stateFor(descriptor, states));
    }
  }
}

/** The descriptors the layer panel lists, in zOrder (the weather layers in the user's order). */
export function controllableLayers<Id extends string>(
  descriptors: readonly LayerDescriptor<Id>[],
  order: readonly string[] = ORDERABLE_LAYER_IDS,
): LayerDescriptor<Id>[] {
  return byZOrder(descriptors, order).filter((descriptor) => descriptor.userControllable);
}

/** A fresh copy of the defaults, for the map store. */
export function defaultLayerStates(): Record<LayerId, LayerState> {
  const out = {} as Record<LayerId, LayerState>;
  for (const id of LAYER_IDS) {
    out[id] = { ...LAYER_DEFAULTS[id] };
  }
  return out;
}

/** `order` with `id` moved one step up (1, towards the top) or down (-1). */
export function moveInOrder<T extends string>(order: readonly T[], id: T, direction: 1 | -1): T[] {
  const index = order.indexOf(id);
  const target = index + direction;
  if (index === -1 || target < 0 || target >= order.length) {
    return [...order];
  }
  const next = [...order];
  next[index] = order[target] as T;
  next[target] = id;
  return next;
}
