export type SpotterMotion = { kind: "stationary" } | { kind: "heading"; degrees: number };

/** A recent public position shared by a Spotter Network participant. */
export interface SpotterPosition {
  latitude: number;
  longitude: number;
  updatedAt: string;
  chaseCam: boolean;
  displayName?: string;
  motion?: SpotterMotion;
}

export interface SpotterPositions {
  checkedAt: string;
  streamersAvailable: boolean;
  positions: readonly SpotterPosition[];
}
