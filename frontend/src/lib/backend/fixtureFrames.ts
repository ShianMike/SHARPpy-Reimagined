// The frame fixtures the browser transport serves (DESIGN-contracts C8):
// the committed SHRF frames, relabelled with the request's own identity — a
// site id or a time, which echoes what was asked. Provider strings (labels,
// units) come from the committed fixtures or the catalog fixture, never from
// a string written here.

import radarSiteShrfUrl from "@contracts/public/map/radar-site.shrf?url&no-inline";
import satelliteShrfUrl from "@contracts/public/map/satellite.shrf?url&no-inline";
import satelliteVisibleShrfUrl from "@contracts/public/map/satellite.visible.shrf?url&no-inline";
import mapCatalog from "@contracts/public/map/catalog.json";
import reportsAll from "@contracts/public/map/reports.json";
import reportsTornado from "@contracts/public/map/reports.tornado.json";
import reportsHail from "@contracts/public/map/reports.hail.json";
import reportsWind from "@contracts/public/map/reports.wind.json";
import reportsSignificant from "@contracts/public/map/reports.significant.json";
import reportOptions from "@contracts/public/map/reports-options.json";
import contoursFixture from "@contracts/public/map/contours.json";
import surfaceFixture from "@contracts/public/map/surface.json";
import thresholdFixture from "@contracts/public/soundings/threshold.example.json";
import { encodeShrf, parseShrf } from "../contracts/frames";

/** A request's own time, hours on: relabelling only, never a derivation. */
export const hoursLater = (iso: string, hours: number): string =>
  new Date(Date.parse(iso) + hours * 3_600_000).toISOString().replace(/\.\d{3}Z$/, "Z");

/** The fixture single-site frame, relabelled for the requested site and product. */
export async function siteFrameFor(args: Readonly<Record<string, unknown>> | undefined): Promise<ArrayBuffer> {
  const frame = parseShrf(await (await fetch(radarSiteShrfUrl)).arrayBuffer());
  const product = typeof args?.product === "string" ? args.product : "reflectivity";
  const site = typeof args?.site === "string" ? args.site : "KTLX";
  const spec = mapCatalog.siteProducts.find((item) => item.key === product);
  const fallbackLabel = typeof frame.header.label === "string" ? frame.header.label : "";
  const fallbackUnits = typeof frame.header.units === "string" ? frame.header.units : "";
  const header = {
    ...frame.header,
    product,
    siteId: site,
    label: spec?.label ?? fallbackLabel,
    units: spec?.units ?? fallbackUnits,
  };
  return encodeShrf(header, frame.body);
}

/** The fixture GOES frame for a channel, with the request's time. */
export async function satelliteFrameFor(args: Readonly<Record<string, unknown>> | undefined): Promise<ArrayBuffer> {
  const visible = args?.channel === "visible";
  const frame = parseShrf(await (await fetch(visible ? satelliteVisibleShrfUrl : satelliteShrfUrl)).arrayBuffer());
  const fallback = typeof frame.header.validTime === "string" ? frame.header.validTime : "";
  const validTime = typeof args?.validTime === "string" ? args.validTime : fallback;
  const header = { ...frame.header, validTime, requestedTime: validTime };
  return encodeShrf(header, frame.body);
}

const REPORT_FIXTURES: Readonly<Record<string, unknown>> = {
  all: reportsAll,
  tornado: reportsTornado,
  hail: reportsHail,
  wind: reportsWind,
  significant: reportsSignificant,
};

/**
 * The fixture storm reports for a hazard filter. Each filter's records are the
 * exporter's own output (one committed file per filter, as the outlooks are);
 * the window relabels its span and its label from the options fixture. As with
 * `fieldFor`'s synthetic field, the committed records are the exporter's
 * default-window set and do not narrow with the requested window — the e2e
 * asserts the window's label and the filter's records, not window arithmetic.
 */
export function reportsFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const filter = typeof args?.hazardFilter === "string" ? args.hazardFilter : "all";
  const fixture = REPORT_FIXTURES[filter] ?? reportsAll;
  const base = structuredClone(fixture) as Record<string, unknown>;
  const windowS = typeof args?.windowS === "number" ? args.windowS : 21600;
  const label = reportOptions.windows.find((window) => window.seconds === windowS)?.label;
  base.windowS = windowS;
  if (label !== undefined) {
    base.windowLabel = label;
  }
  if (typeof args?.validTime === "string") {
    base.validTime = args.validTime;
  }
  return base;
}

/**
 * The fixture contour geometry. The committed lines are the exporter's own
 * output for `hgt-wind-500`; the request's identity relabels the header, as
 * the field frame's does. Other contoured products draw the same lines, which
 * is synthetic data in a fixture build — the geometry's *shape* is what the
 * layer and its tests read.
 */
export function contoursFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const base = structuredClone(contoursFixture) as Record<string, unknown>;
  if (typeof args?.product === "string") {
    base.product = args.product;
  }
  if (typeof args?.runTime === "string") {
    base.runTime = args.runTime;
  }
  if (typeof args?.forecastHour === "number") {
    base.forecastHour = args.forecastHour;
  }
  return base;
}

/**
 * The fixture station models of one anchor and hour. The committed plots are
 * the exporter's own set around its anchor and do not move with the requested
 * point — the e2e asserts the readouts and the states, not fetch geography.
 * The requested time echoes back, as the frames echo a requested site or time.
 */
export function surfacePlotsFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const base = structuredClone(surfaceFixture) as Record<string, unknown>;
  if (typeof args?.validTime === "string") {
    base.validTime = args.validTime;
  }
  return base;
}

/**
 * The fixture threshold evaluation. The committed verdicts are the exporter's
 * own and do not move with the requested rule or point -- the e2e asserts the
 * classification readouts and the states (one qualifying, two not, one
 * member's download failed), not fetch geography. The requested run's valid
 * time echoes back, as the frames echo a requested site or time.
 */
export function thresholdFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const base = structuredClone(thresholdFixture) as Record<string, unknown>;
  const runTime = typeof args?.runTime === "string" ? args.runTime : null;
  const forecastHour = typeof args?.forecastHour === "number" ? args.forecastHour : 0;
  if (runTime !== null && base.validTime !== null) {
    base.validTime = hoursLater(runTime, forecastHour);
  }
  return base;
}
