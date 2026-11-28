// Contour geometry DTOs, pinned by src-tauri/src/dto/contours.rs.
import type { Bounds } from "./soundings";

/** A product's contour lines: a level set of its own contour field. */
export interface ProductContour {
  /** The level spacing in `label` units. */
  interval: number;
  label: string;
  colour: string;
  width: number;
}

/** Joined display-path points: [lon, lat] in order. */
export type ContourPath = readonly (readonly [number, number])[];

/** One native contour level at its geographic label anchor. */
export interface ContourLabel {
  position: readonly [number, number];
  value: number;
}

/** A surface-pressure extreme marked H or L, with its center pressure. */
export interface PressureCenter {
  lon: number;
  lat: number;
  kind: "H" | "L";
  value: number;
}

/** One 1000-500 mb thickness contour: its level as joined geographic points. */
export interface ThicknessPath {
  /** The contour level in `unit` units, e.g. 540 dam. */
  value: number;
  points: ContourPath;
}

/** The 1000-500 mb thickness level set: dashed cold and warm lines over pressure. */
export interface ThicknessContours {
  unit: string;
  interval: number;
  paths: readonly ThicknessPath[];
  labels: readonly ContourLabel[];
}

/** A product's isolines, as lon/lat segments or joined display paths. */
export interface ContourLines {
  product: string;
  runTime: string;
  forecastHour: number;
  unit: string;
  interval: number;
  segments: readonly (readonly [readonly [number, number], readonly [number, number]])[];
  /** Joined display paths, when provided instead of independent segments. */
  paths?: readonly ContourPath[];
  /** Optional native contour levels at their geographic label anchors. */
  labels?: readonly ContourLabel[];
  /** H/L pressure centers at this frame's pressure extremes. */
  centers?: readonly PressureCenter[];
  /** The frame's 1000-500 mb thickness level set, over the pressure lines. */
  thickness?: ThicknessContours;
  wind: WindBarbGrid | null;
}

/** Display winds; row 0 is north. Low 9 bits: from degrees; high 7: speed / 5 kt; 65535: missing. */
export interface WindBarbGrid {
  width: number;
  height: number;
  bounds: Bounds;
  data: Uint16Array;
}
