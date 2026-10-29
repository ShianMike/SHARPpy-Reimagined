// Map data DTOs (src-tauri/src/dto/map.rs), pinned by src-tauri/contracts/public/map/.
// The backend sends numbers and geography; the frontend chooses every pixel.

import type { Station } from "./contracts";
import type { Bounds } from "./soundings";

/** A sharpmod colour scale: `[value, "#rrggbb"]` stops in display units, ascending. */
export interface Palette {
  display: "auto" | "fixed" | "temperature-difference";
  stops: readonly (readonly [number, string])[];
  units: string;
  /** Banded: each class takes its stop's flat colour; otherwise RGB between stops. */
  stepped: boolean;
  /** Values below are hidden; null hides values below the first stop. */
  floor: number | null;
  /** Values above are hidden; null clips them to the last colour. */
  ceiling: number | null;
  ticks: readonly number[];
}

export interface FieldProduct {
  key: string;
  label: string;
  shortLabel: string;
  category: string;
  description: string;
  caveat: string;
  /** First forecast hour with a complete product interval. */
  minimumForecastHour: number;
  palette: Palette;
  /** The product's contour-line spec; null for products drawn as colour only. */
  contour: ProductContour | null;
  /** Wind barbs in knots, independent of the shaded field's quantity. */
  wind: boolean;
}

/** A product's contour lines: a level set of its own contour field. */
export interface ProductContour {
  /** The level spacing in `label` units. */
  interval: number;
  label: string;
  colour: string;
  width: number;
}

/** The isolines of one product's contour field, as two-point lon/lat segments. */
export interface ContourLines {
  product: string;
  runTime: string;
  forecastHour: number;
  unit: string;
  interval: number;
  segments: readonly (readonly [readonly [number, number], readonly [number, number]])[];
  wind: WindBarbGrid | null;
}

/** Display winds; row 0 is north. Low 9 bits: from degrees; high 7: speed / 5 kt; 65535: missing. */
export interface WindBarbGrid {
  width: number;
  height: number;
  bounds: Bounds;
  data: Uint16Array;
}

export interface RadarProduct {
  key: string;
  label: string;
  units: string;
  description: string;
}

/** The imagery channels one GOES frame can be rendered from. */
export const SATELLITE_CHANNELS = ["infrared", "visible"] as const;
export type SatelliteChannel = (typeof SATELLITE_CHANNELS)[number];

export const OUTLOOK_KEYS = ["cat", "torn", "wind", "hail"] as const;
export type OutlookKey = (typeof OUTLOOK_KEYS)[number];
/** The layer names the panel and the inspector show. */
export const OUTLOOK_NAMES: Readonly<Record<OutlookKey, string>> = {
  cat: "SPC categorical",
  torn: "SPC tornado",
  wind: "SPC wind",
  hail: "SPC hail",
};

export interface OutlookProduct {
  key: string;
  label: string;
}

export interface MapCatalog {
  /** The selected model whose supported fields this catalogue describes. */
  fieldModel: string;
  fieldBounds: Bounds;
  /** The last forecast hour of each cycle, 00Z to 23Z. */
  maxForecastHours: readonly number[];
  categories: readonly string[];
  defaultProduct: string;
  products: readonly FieldProduct[];
  radarProducts: readonly RadarProduct[];
  /** The products one antenna publishes, distinct from the mosaic's. */
  siteProducts: readonly RadarProduct[];
  outlookProducts: readonly OutlookProduct[];
}

/**
 * One field at one run and hour: `width * height` cells, row 0 at the north
 * edge, rows evenly spaced in latitude; `value = cell * scale + offset`.
 */
export interface FieldFrame {
  product: string;
  runTime: string;
  forecastHour: number;
  validTime: string;
  width: number;
  height: number;
  bounds: Bounds;
  units: string;
  scale: number;
  offset: number;
  /** The cell value that means no data. */
  nodata: number;
  valueMin: number;
  valueMax: number;
  sourceUrl: string;
  attribution: string;
  cells: Uint16Array;
}

/** The newest radar mosaic frame: the service's PNG over lon/lat bounds. */
export interface RadarFrame {
  /** Provider legend, or the same numeric palette used to render velocity. */
  legendPng?: string | null;
  palette?: Palette | null;
  product: string;
  label: string;
  units: string;
  bounds: Bounds;
  retrievedAt: string | null;
  validTime: string | null;
  updateIntervalS: number;
  attribution: string;
  sourceUrl: string;
  png: Blob;
}

/** One antenna's own frame: the mosaic's shape, plus the serving site. */
export interface RadarSiteFrame extends RadarFrame {
  siteId: string;
  elevationAngle?: number | null;
}

// NOAA RPCCDS Level III cuts; several VCPs share a cut with different actual angles.
export const RADAR_TILTS = [
  { value: "z", label: "0.3° / 0.4°" }, { value: "0", label: "0.5°" },
  { value: "a", label: "0.9°" }, { value: "1", label: "1.3° / 1.5°" },
  { value: "b", label: "1.8°" }, { value: "2", label: "2.4° / 2.5°" },
  { value: "3", label: "3.1° / 3.4° / 3.5°" },
] as const;
export type RadarTilt = (typeof RADAR_TILTS)[number]["value"];
export const RADAR_TILTABLE_PRODUCTS = ["reflectivity", "velocity", "correlation-coefficient"] as const;

export interface RadarTilts {
  siteId: string;
  product: (typeof RADAR_TILTABLE_PRODUCTS)[number];
  checkedAt: string;
  tilts: readonly { tilt: RadarTilt; status: "available" | "unavailable" | "unknown" }[];
}

/** One radar antenna, with optional provider capabilities for the site picker. */
export interface RadarSite {
  id: string;
  latitude: number;
  longitude: number;
  name?: string;
  country?: string;
  products?: readonly string[];
}

export interface RadarSiteCatalog {
  sites: readonly RadarSite[];
}

/**
 * One rendered GOES frame over the map's extent. `requestedTime` is what was
 * asked for and `validTime` the actual scan within the provider's tolerance;
 * `coverageFraction` is the share of the frame the scan covers, so uncovered
 * ground reads as uncovered.
 */
export interface SatelliteFrame {
  product: string;
  satellite: string;
  label: string;
  units: string;
  bounds: Bounds;
  requestedTime: string;
  retrievedAt: string | null;
  validTime: string;
  updateIntervalS: number;
  coverageFraction: number;
  nativeResolutionKm: number;
  attribution: string;
  sourceUrl: string;
  png: Blob;
}

/** One local storm report: what it is, where, when, and how to mark it. */
export interface ReportPoint {
  label: string;
  description: string;
  /** The marker's fill; null for a symbol the provider draws unfilled. */
  fill: string | null;
  stroke: string;
  /** The provider's own screen-marker spec: radius in CSS pixels and a symbol name. */
  radiusPx: number;
  symbol: string;
  caption: string;
  significant: boolean;
  category: string;
  valid: string | null;
  longitude: number;
  latitude: number;
}

export interface StormReports {
  validTime: string;
  /** The total query span in seconds. */
  windowS: number;
  windowLabel: string;
  hazardFilter: string;
  totalCount: number;
  shownCount: number;
  subtitle: string;
  validFrom: string | null;
  validTo: string | null;
  sourceUrl: string;
  attribution: string;
  reports: readonly ReportPoint[];
}

/** The provider's own window and filter labels, for the layer's controls. */
export interface ReportOptions {
  windows: readonly { label: string; seconds: number }[];
  filters: readonly { key: string; label: string }[];
}

export interface OutlookArea {
  label: string;
  description: string;
  /** SPC's fill colour; null for an area SPC draws unfilled. */
  fill: string | null;
  stroke: string;
  rank: number;
  /** SPC hatches the area (significant severe). */
  hatch: boolean;
  hatchLevel: number;
  /** The exterior ring, then holes; `[lon, lat]` points, not closed. */
  rings: readonly (readonly (readonly [number, number])[])[];
}

export interface Outlook {
  title: string;
  subtitle: string;
  validFrom: string | null;
  validTo: string | null;
  issued: string | null;
  /** False when the nearest outlook found does not cover the requested time. */
  covers: boolean;
  sourceUrl: string;
  attribution: string;
  areas: readonly OutlookArea[];
}

export interface SpcOutlook {
  product: string;
  validTime: string | null;
  /** Null when SPC published nothing for the time. */
  outlook: Outlook | null;
}

export interface Place {
  name: string;
  latitude: number;
  longitude: number;
  /** 1: a major city; 2: a town. */
  tier: 1 | 2;
}

export interface PlaceLabels {
  places: readonly Place[];
}

/** The stations UWyo lists for one observation time. */
export interface AvailabilityReport {
  observationTime: string;
  /** Bundled stations that are listed. */
  listedIds: readonly string[];
  /** Listed stations the bundled catalogue does not have. */
  extraStations: readonly Station[];
  droppedCount: number;
}
