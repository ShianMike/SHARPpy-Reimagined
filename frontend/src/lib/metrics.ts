// User Timing marks (DESIGN-frontend F9 rule 8). Each is set once per page
// load, so desktop measurements read the first occurrence of each milestone.

export const MARKS = {
  shellPainted: "sharppy:shell-painted",
  mapLoaded: "sharppy:map-loaded",
  basemapLoaded: "sharppy:basemap-loaded",
  catalogRequested: "sharppy:catalog-requested",
  catalogReceived: "sharppy:catalog-received",
  stationsRendered: "sharppy:stations-rendered",
} as const;

export type MarkName = (typeof MARKS)[keyof typeof MARKS];

/** Sets the mark unless it already exists. Returns true when it was set now. */
export function markOnce(name: MarkName): boolean {
  if (typeof performance === "undefined" || performance.getEntriesByName(name, "mark").length > 0) {
    return false;
  }
  performance.mark(name);
  return true;
}

export function hasMark(name: MarkName): boolean {
  return typeof performance !== "undefined" && performance.getEntriesByName(name, "mark").length > 0;
}
