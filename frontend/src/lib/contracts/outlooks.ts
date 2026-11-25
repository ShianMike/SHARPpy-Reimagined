import { WPC_PRODUCTS, type Outlook, type OutlookArea, type OutlookDiscussion, type OutlookDiscussionSection, type SpcOutlook, type WpcOutlook, type WpcProduct } from "../../types/outlooks";
import {
  at, ContractError, expectArray, expectBoolean, expectFiniteNumber, expectInteger, expectNullable,
  expectObject, expectExactKeys, expectString, expectTimestamp,
} from "./guards";

const COLOUR = /^#[0-9A-Fa-f]{6}$/;

function colour(value: unknown, path: string): string {
  const raw = expectString(value, path, { minLength: 7, maxLength: 7 });
  if (!COLOUR.test(raw)) {
    throw new ContractError(path, `expected a #rrggbb colour, got ${JSON.stringify(raw)}`);
  }
  return raw;
}

function lonLat(value: unknown, path: string): readonly [number, number] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new ContractError(path, "expected a [lon, lat] pair");
  }
  return [
    expectFiniteNumber(value[0], at(path, 0), { min: -180, max: 180 }),
    expectFiniteNumber(value[1], at(path, 1), { min: -90, max: 90 }),
  ];
}

export function parseOutlookArea(value: unknown, path: string): OutlookArea {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["label", "description", "fill", "stroke", "rank", "hatch", "hatchLevel", "rings"]);
  return {
    label: expectString(obj.label, at(path, "label"), { maxLength: 120 }),
    description: expectString(obj.description, at(path, "description"), { maxLength: 300 }),
    fill: expectNullable(obj.fill, at(path, "fill"), colour),
    stroke: colour(obj.stroke, at(path, "stroke")),
    rank: expectInteger(obj.rank, at(path, "rank")),
    hatch: expectBoolean(obj.hatch, at(path, "hatch")),
    hatchLevel: expectInteger(obj.hatchLevel, at(path, "hatchLevel"), { min: 0, max: 255 }),
    rings: expectArray(obj.rings, at(path, "rings"), (ring, p) => expectArray(ring, p, lonLat)),
  };
}

function parseDiscussionSection(value: unknown, path: string): OutlookDiscussionSection {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["heading", "body"]);
  return {
    heading: expectString(obj.heading, at(path, "heading"), { minLength: 1, maxLength: 200 }),
    body: expectString(obj.body, at(path, "body"), { minLength: 1, maxLength: 12_000 }),
  };
}

export function parseOutlookDiscussion(value: unknown, path: string): OutlookDiscussion {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["headline", "summary", "sections", "issued", "sourceUrl"]);
  const sections = expectArray(obj.sections, at(path, "sections"), parseDiscussionSection);
  if (sections.length > 24) {
    throw new ContractError(at(path, "sections"), "expected at most 24 discussion sections");
  }
  return {
    headline: expectString(obj.headline, at(path, "headline"), { maxLength: 600 }),
    summary: expectString(obj.summary, at(path, "summary"), { maxLength: 1_500 }),
    sections,
    issued: expectNullable(obj.issued, at(path, "issued"), expectTimestamp),
    sourceUrl: expectString(obj.sourceUrl, at(path, "sourceUrl"), { minLength: 1, maxLength: 500 }),
  };
}

function parseOutlook(value: unknown, path: string): Outlook {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "title", "subtitle", "validFrom", "validTo", "issued", "covers", "sourceUrl", "attribution", "areas",
  ], ["affectedStates", "discussion"]);
  const time = (v: unknown, p: string): string | null => expectNullable(v, p, expectTimestamp);
  return {
    title: expectString(obj.title, at(path, "title"), { maxLength: 300 }),
    subtitle: expectString(obj.subtitle, at(path, "subtitle"), { maxLength: 300 }),
    validFrom: time(obj.validFrom, at(path, "validFrom")),
    validTo: time(obj.validTo, at(path, "validTo")),
    issued: time(obj.issued, at(path, "issued")),
    covers: expectBoolean(obj.covers, at(path, "covers")),
    sourceUrl: expectString(obj.sourceUrl, at(path, "sourceUrl"), { minLength: 1, maxLength: 500 }),
    attribution: expectString(obj.attribution, at(path, "attribution"), { maxLength: 300 }),
    ...(obj.affectedStates === undefined ? {} : {
      affectedStates: expectArray(obj.affectedStates, at(path, "affectedStates"), (state, p) =>
        expectString(state, p, { minLength: 1, maxLength: 32 })),
    }),
    ...(obj.discussion === undefined ? {} : {
      discussion: parseOutlookDiscussion(obj.discussion, at(path, "discussion")),
    }),
    areas: expectArray(obj.areas, at(path, "areas"), parseOutlookArea),
  };
}

export function parseSpcOutlook(value: unknown, path = ""): SpcOutlook {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["product", "validTime", "outlook"]);
  return {
    product: expectString(obj.product, at(path, "product"), { minLength: 1, maxLength: 40 }),
    validTime: expectNullable(obj.validTime, at(path, "validTime"), expectTimestamp),
    outlook: expectNullable(obj.outlook, at(path, "outlook"), parseOutlook),
  };
}

export function parseWpcOutlook(value: unknown, path = ""): WpcOutlook {
  const parsed = parseSpcOutlook(value, path);
  if (!WPC_PRODUCTS.includes(parsed.product as WpcProduct)) {
    throw new ContractError(at(path, "product"), `expected one of ${WPC_PRODUCTS.join(", ")}, got ${JSON.stringify(parsed.product)}`);
  }
  return parsed;
}
