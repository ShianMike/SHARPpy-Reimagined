import type { Bounds } from "./soundings";

export interface BoxSoundingMetadata {
  bounds: Bounds;
  widthKm: number;
  heightKm: number;
  areaKm2: number;
  gridResolutionKm: number;
  gridType: string;
  selectedColumns: number;
  validColumns: number;
  surfacePressureHpa: number;
  method: string;
  location: string;
  sourceCacheHit: boolean;
  profileCacheHit: boolean;
  coverage: readonly { pressureHpa: number; validColumns: number; fraction: number; retained: boolean }[];
  timings: Readonly<Record<string, number>>;
}
