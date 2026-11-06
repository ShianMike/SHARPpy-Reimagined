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

/** A product's isolines, as lon/lat segments or joined display paths. */
export interface ContourLines {
  product: string;
  runTime: string;
  forecastHour: number;
  unit: string;
  interval: number;
  segments: readonly (readonly [readonly [number, number], readonly [number, number]])[];
  /** Joined display paths, when provided instead of independent segments. */
  paths?: readonly (readonly (readonly [number, number])[])[];
  /** Optional native contour levels at their geographic label anchors. */
  labels?: readonly { position: readonly [number, number]; value: number }[];
  wind: WindBarbGrid | null;
}

/** Display winds; row 0 is north. Low 9 bits: from degrees; high 7: speed / 5 kt; 65535: missing. */
export interface WindBarbGrid {
  width: number;
  height: number;
  bounds: Bounds;
  data: Uint16Array;
}
