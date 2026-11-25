export const OUTLOOK_KEYS = ["cat", "torn", "wind", "hail"] as const;
export type OutlookKey = (typeof OUTLOOK_KEYS)[number];
export const OUTLOOK_DAYS = [1, 2, 3] as const;
export type OutlookDay = (typeof OUTLOOK_DAYS)[number];
export type OutlookProductKey = OutlookKey | "prob";
export const WPC_OUTLOOK_KEY = "ero" as const;
export const WPC_WINTER_KEY = "winter" as const;
export const WPC_WINTER_PRODUCTS = [
  "winter-overall",
  "winter-snow",
  "winter-snow-load",
  "winter-ice",
  "winter-blowing-snow",
] as const;
export type WpcWinterProduct = (typeof WPC_WINTER_PRODUCTS)[number];
export const WPC_WINTER_PRODUCT_LABELS: Readonly<Record<WpcWinterProduct, { short: string; label: string }>> = {
  "winter-overall": { short: "Overall", label: "Overall impact" },
  "winter-snow": { short: "Snow", label: "Snow amount" },
  "winter-ice": { short: "Ice", label: "Ice accumulation" },
  "winter-snow-load": { short: "Load", label: "Snow load" },
  "winter-blowing-snow": { short: "Blowing", label: "Blowing snow" },
};
export const WPC_PRODUCTS = ["ero", ...WPC_WINTER_PRODUCTS] as const;
export type WpcProduct = (typeof WPC_PRODUCTS)[number];
export type OutlookLayerKey = OutlookKey | typeof WPC_OUTLOOK_KEY | typeof WPC_WINTER_KEY;
export type WpcOutlookDayMode = "auto" | OutlookDay;
export const WPC_OUTLOOK_NAME = "WPC excessive rainfall";
export const WPC_WINTER_NAME = "WPC winter weather";

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

export interface OutlookArea {
  label: string;
  description: string;
  /** Provider's fill colour; null for an area drawn unfilled. */
  fill: string | null;
  stroke: string;
  rank: number;
  /** SPC hatches the area (significant severe). */
  hatch: boolean;
  hatchLevel: number;
  /** The exterior ring, then holes; `[lon, lat]` points, not closed. */
  rings: readonly (readonly (readonly [number, number])[])[];
}

export interface OutlookDiscussionSection {
  heading: string;
  body: string;
}

export interface OutlookDiscussion {
  headline: string;
  summary: string;
  sections: readonly OutlookDiscussionSection[];
  issued: string | null;
  sourceUrl: string;
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
  /** States with some coverage in the highest risk tier of a latest issuance. */
  affectedStates?: readonly string[];
  discussion?: OutlookDiscussion;
  areas: readonly OutlookArea[];
}

export interface SpcOutlook {
  product: string;
  validTime: string | null;
  /** Null when the provider published nothing for the time. */
  outlook: Outlook | null;
}

export type WpcOutlook = SpcOutlook;
