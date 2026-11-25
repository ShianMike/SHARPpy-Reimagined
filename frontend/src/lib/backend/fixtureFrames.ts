// The frame fixtures the browser transport serves (DESIGN-contracts C8):
// the committed SHRF frames, relabelled with the request's own identity — a
// site id or a time, which echoes what was asked. Provider strings (labels,
// units) come from the committed fixtures or the catalog fixture, never from
// a string written here.

import radarSiteShrfUrl from "@contracts/public/map/radar-site.shrf?url&no-inline";
import satelliteShrfUrl from "@contracts/public/map/satellite.shrf?url&no-inline";
import satelliteVisibleShrfUrl from "@contracts/public/map/satellite.visible.shrf?url&no-inline";
import mapCatalog from "@contracts/public/map/catalog.json";
import { RADAR_TILTABLE_PRODUCTS } from "../../types/map";
import correlationPalette from "@contracts/public/map/radar-correlation.palette.json";
import echoTopsPalette from "@contracts/public/map/radar-echo-tops.palette.json";
import velocityPalette from "@contracts/public/map/radar-velocity.palette.json";
import reflectivityPalette from "@contracts/public/map/radar-base-reflectivity.palette.json";
import reportsAll from "@contracts/public/map/reports.json";
import reportsTornado from "@contracts/public/map/reports.tornado.json";
import reportsHail from "@contracts/public/map/reports.hail.json";
import reportsWind from "@contracts/public/map/reports.wind.json";
import reportsSignificant from "@contracts/public/map/reports.significant.json";
import reportOptions from "@contracts/public/map/reports-options.json";
import outlookCat from "@contracts/public/map/outlook.cat.json";
import outlookTorn from "@contracts/public/map/outlook.torn.json";
import outlookWind from "@contracts/public/map/outlook.wind.json";
import contoursFixture from "@contracts/public/map/contours.json";
import surfaceFixture from "@contracts/public/map/surface.json";
import thresholdFixture from "@contracts/public/soundings/threshold.example.json";
import wpcOutlookFixtures from "./wpcOutlookFixture.json";
import { encodeShrf, parseShrf } from "../contracts/frames";

/** A request's own time, hours on: relabelling only, never a derivation. */
export const hoursLater = (iso: string, hours: number): string =>
  new Date(Date.parse(iso) + hours * 3_600_000).toISOString().replace(/\.\d{3}Z$/, "Z");
const tiltAngles: Readonly<Record<string, number>> = { x: -0.2, y: 0, z: 0.3, "0": 0.5, a: 0.9, "1": 1.3, b: 1.8, "2": 2.4, "3": 3.1 };

/** The fixture single-site frame, relabelled for the requested site and product. */
export async function siteFrameFor(args: Readonly<Record<string, unknown>> | undefined): Promise<ArrayBuffer> {
  const frame = parseShrf(await (await fetch(radarSiteShrfUrl)).arrayBuffer());
  const product = typeof args?.product === "string" ? args.product : "reflectivity";
  const site = typeof args?.site === "string" ? args.site : "KTLX";
  const tilt = typeof args?.tilt === "string" ? args.tilt : "0";
  const spec = mapCatalog.siteProducts.find((item) => item.key === product);
  const fallbackLabel = typeof frame.header.label === "string" ? frame.header.label : "";
  const fallbackUnits = typeof frame.header.units === "string" ? frame.header.units : "";
  const header = {
    ...frame.header,
    product,
    siteId: site,
    elevationAngle: RADAR_TILTABLE_PRODUCTS.some((key) => key === product) ? (tiltAngles[tilt] ?? 0.5) : null,
    validTime: typeof args?.validTime === "string" ? args.validTime : frame.header.validTime,
    label: spec?.label ?? fallbackLabel,
    units: spec?.units ?? fallbackUnits,
    palette: product === "velocity" ? velocityPalette : product === "reflectivity" ? reflectivityPalette : product === "correlation-coefficient" ? correlationPalette : product === "echo-tops" ? echoTopsPalette : null,
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

const OUTLOOKS: Readonly<Record<string, unknown>> = { cat: outlookCat, torn: outlookTorn, wind: outlookWind };

/** The fixture outlook of a product, for the requested time. */
export function outlookFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const product = String(args?.product);
  const validTime = String(args?.validTime);
  if (args?.day !== undefined && args.day !== 1) return { product, validTime, outlook: null };
  const base = structuredClone(OUTLOOKS[product] ?? { ...outlookWind, product }) as Record<string, unknown>;
  const outlook = base.outlook as {
    validFrom: string;
    validTo: string;
    issued: string | null;
    covers: boolean;
    discussion?: unknown;
  } | null;
  if (outlook !== null) {
    const t = Date.parse(validTime);
    outlook.covers = Date.parse(outlook.validFrom) <= t && t < Date.parse(outlook.validTo);
    if (args?.day === 1) {
      outlook.discussion = {
        headline: "THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS PORTIONS OF THE SOUTHERN PLAINS",
        summary: "Scattered severe storms capable of damaging gusts and large hail may develop through evening across portions of the central and southern Plains.",
        sections: [{ heading: "Synopsis", body: "An upper-level trough over the High Plains will advance eastward through the evening, supporting organized thunderstorm development along a dryline." }],
        issued: outlook.issued,
        sourceUrl: "https://www.spc.noaa.gov/products/outlook/day1otlk.html",
      };
    }
  }
  return { ...base, validTime };
}

/** The fixture WPC Excessive Rainfall or Winter Weather Outlook for the requested time or issuance day. */
export function wpcOutlookFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const validTime = String(args?.validTime);
  const product = typeof args?.product === "string" ? args.product : "ero";
  const winter = product.startsWith("winter-");
  const day = typeof args?.day === "number" ? args.day : 1;
  if (!winter && args?.day !== undefined && day !== 1) return { product, validTime, outlook: null };
  const ref = outlookCat.outlook;
  const start = Date.parse(ref.validFrom);
  const end = Date.parse(ref.validTo);
  const t = Date.parse(validTime);
  const covers = start <= t && t < end;
  const sourceUrl = winter
    ? "https://www.wpc.ncep.noaa.gov/wwd/wssi/wssi.php"
    : `https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day=${String(day)}`;
  const rawAreas = winter ? wpcOutlookFixtures.winter : wpcOutlookFixtures.ero;
  const winterLabels: Readonly<Record<string, string>> = {
    "winter-overall": "Overall impact",
    "winter-snow": "Snow amount",
    "winter-ice": "Ice accumulation",
    "winter-snow-load": "Snow load",
    "winter-blowing-snow": "Blowing snow",
  };
  return {
    product,
    validTime,
    outlook: {
      title: winter
        ? `WPC Day ${String(day)} Winter Weather Outlook — ${winterLabels[product] ?? "Overall impact"}`
        : `WPC Day ${String(day)} Excessive Rainfall Outlook`,
      subtitle: ref.subtitle.replace(/^Day \d+/, `Day ${String(day)}${winter ? ` · ${winterLabels[product] ?? "Overall impact"}` : ""}`),
      validFrom: ref.validFrom,
      validTo: ref.validTo,
      issued: ref.issued,
      covers,
      sourceUrl,
      attribution: "NOAA/NWS Weather Prediction Center",
      ...(args?.day === undefined ? {} : {
        affectedStates: winter ? ["Illinois", "Missouri"] : ["Oklahoma", "Texas"],
        discussion: winter
          ? {
            headline: "Significant snowfall likely to produce hazardous travel impacts in eastern Missouri and western Illinois",
            summary: "Steady snowfall rates around 1 to 2 inches per hour may accumulate 6 to 10 inches across eastern Missouri and western Illinois overnight into Day 2.",
            sections: [{ heading: `Middle Mississippi Valley (Day ${String(day)})`, body: "Strong frontal forcing and deep moisture transport will sustain wet accumulating snow along the Mississippi River corridor." }],
            issued: ref.issued,
            sourceUrl,
          }
          : {
            headline: "THERE IS A SLIGHT RISK OF EXCESSIVE RAINFALL ACROSS PORTIONS OF THE SOUTHERN PLAINS",
            summary: "Repeated heavy rain bands may produce localized totals of 2 to 4 inches and isolated flash flooding across eastern Oklahoma and north Texas.",
            sections: [{ heading: "Southern Plains", body: "Persistent low-level moisture transport will sustain high rainfall rates along a slow-moving frontal boundary." }],
            issued: ref.issued,
            sourceUrl,
          },
      }),
      areas: rawAreas.map((area, index) => ({
        ...area,
        rings: ref.areas[index]?.rings ?? ref.areas[0]?.rings ?? [],
      })),
    },
  };
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

export function latestSurfacePlotsFixture(): unknown {
  const { validTime, counts, plots } = structuredClone(surfaceFixture);
  return {
    validTime,
    counts: { ...counts, nearby: counts.matched, queried: counts.matched, noObservation: 0, failed: 0, unqueried: 0 },
    plots: plots.filter((plot) => plot.availability === "available"),
  };
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
