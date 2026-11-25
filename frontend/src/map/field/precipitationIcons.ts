// Symbols share the REFS palette's rain, snow, freezing-rain and mix order.
// Each path uses a 24 × 24 view box for both the map and canvas export.
export const PRECIPITATION_ICON_PATHS = [
  "M12 3C9 7 6 10.5 6 14a6 6 0 0 0 12 0c0-3.5-3-7-6-11Z",
  "M12 2v20M3.3 7l17.4 10M3.3 17l17.4-10M9 4l3 3 3-3M9 20l3-3 3 3M4 10l3-1V6M20 14l-3 1v3M4 14l3 1v3M20 10l-3-1V6",
  "M12 2C8.5 6.5 5 10.5 5 14a7 7 0 0 0 14 0c0-3.5-3.5-7.5-7-12ZM12 10v8m-3.5-6 7 4m-7 0 7-4",
  "M6 14a3.5 3.5 0 0 1 .3-7A5.5 5.5 0 0 1 17 7.5h1a3.25 3.25 0 0 1 0 6.5H6ZM8.5 17l-2 4M15.5 16.5v6m-2.6-4.5 5.2 3m-5.2 0 5.2-3",
] as const;
